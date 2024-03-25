#!/usr/bin/env python3
import argparse
import logging
from argparse import ArgumentParser as AP
from os.path import splitext
from pathlib import Path

import jax
import numpy as np
from aicsimageio import AICSImage
from basicpy import BaSiC
from skimage.io import imsave

logger = logging.Logger("basicpy-docker-mcmicro")
logger.setLevel(logging.INFO)


def get_args():
    # Script description
    description = """Calculate the flatfield and darkfield of a RAW image using the BaSiC algorithm."""

    # Add parser
    parser = AP(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Sections
    inputs = parser.add_argument_group(
        title="Required Input", description="Paths to required inputs"
    )

    inputs.add_argument(
        "-i",
        "--input",
        dest="input",
        action="store",
        required=True,
        help="Path to input file",
    )

    output = parser.add_argument_group(
        title="Output", description="Paths to output file"
    )
    output.add_argument(
        "-o",
        "--output_folder",
        dest="output_folder",
        action="store",
        required=True,
        help="Path to output folder",
    )

    optional = parser.add_argument_group(
        title="Optional Input for the tool",
        description="Optional arguments for the tool",
    )
    optional.add_argument(
        "-sf",
        "--smoothness_flatfield",
        dest="smoothness_flatfield",
        action="store",
        required=False,
        type=float,
        default=2.5,
        help="Larger value makes the flatfield smoother.",
    )
    optional.add_argument(
        "-sd",
        "--smoothness_darkfield",
        dest="smoothness_darkfield",
        action="store",
        required=False,
        type=float,
        default=5.0,
        help="Larger value makes the darkfield smoother.",
    )
    optional.add_argument(
        "-sc",
        "--sparse_cost_darkfield",
        dest="sparse_cost_darkfield",
        action="store",
        required=False,
        type=float,
        default=0.01,
        help="Larger value encorages the darkfield sparseness.",
    )
    optional.add_argument(
        "-mi",
        "--max_reweight_iterations",
        dest="max_reweight_iterations",
        action="store",
        required=False,
        type=int,
        default=20,
        help="Maximum number of reweighting iterations.",
    )
    optional.add_argument(
        "-df",
        "--darkfield",
        dest="darkfield",
        action="store_true",
        required=False,
        default=False,
        help="Flag to calculate the darkfield [default=False].",
    )
    optional.add_argument(
        "-a",
        "--autotune",
        dest="autotune",
        action="store_true",
        required=False,
        help="Flag to autotune the parameters [default=False].",
    )
    optional.add_argument(
        "-ie",
        "--ignore_single_image_error",
        dest="ignore_single_image_error",
        action="store_true",
        required=False,
        default=False,
        help="Ignore error for single-sited image [default=False].",
    )
    optional.add_argument(
        "-f",
        "--fitting_mode",
        dest="fitting_mode",
        choices=["ladmap", "approximate"],
        action="store",
        required=False,
        default="ladmap",
        help="Fitting mode to use, ladmap or approximate [default = 'ladmap'].",
    )
    optional.add_argument(
        "-d",
        "--device",
        dest="device",
        choices=["cpu", "gpu"],
        action="store",
        required=False,
        default="cpu",
        help="Device to use, cpu or gpu [default = 'cpu'].",
    )

    arg = parser.parse_args()

    # Convert input and output to Pathlib
    arg.input = Path(arg.input)
    arg.output_folder = Path(arg.output_folder)

    return arg


def main(args):
    # Select device
    if args.device == "cpu":
        jax.config.update("jax_platform_name", "cpu")

    # Run BASIC
    basic = BaSiC(
        smoothness_flatfield=args.smoothness_flatfield,
        smoothness_darkfield=args.smoothness_darkfield,
        sparse_cost_darkfield=args.sparse_cost_darkfield,
        max_reweight_iterations=args.max_reweight_iterations,
        fitting_mode=args.fitting_mode,
        get_darkfield=args.darkfield,
    )

    # Initialize flatfields and darkfields
    flatfields = []
    darkfields = []

    # Check if input is a folder or a file
    if args.input.is_file():
        logger.info(f"opening images at {args.input}")
        image = AICSImage(args.input)
        for channel in range(image.dims.C):
            images_data = []
            for scene in image.scenes:
                image.set_scene(scene)
                images_data.append(image.get_image_data("MTZYX", C=channel))
            images_data = np.array(images_data).reshape(
                [-1, *images_data[0].shape[-2:]]
            )
            if images_data.shape[0] < 2 and not args.ignore_single_image_error:
                raise RuntimeError(
                    "The image is single sited. Was it saved in the correct way?"
                )
            if args.autotune:
                basic.autotune(images_data)
            basic.fit(images_data)
            flatfields.append(basic.flatfield)
            darkfields.append(basic.darkfield)

    # If input is a folder
    else:
        images_data = None
        channels = None
        for image_path in args.input.iterdir():
            logger.info(f"opening images at {image_path}")
            image = AICSImage(image_path)
            if channels is None:
                channels = image.channel_names
                images_data = [[] for _ in len(channels)]
            else:
                assert channels == image.channel_names
        for channel in range(len(channels)):
            images_data = []
            for image_path in args.input.iterdir():
                image = AICSImage(image_path)
                for scene in image.scenes:
                    image.set_scene(scene)
                    images_data.append(image.get_image_data("MTZYX", C=channel))
            images_data = np.array(images_data).reshape(
                [-1, *images_data[0].shape[-2:]]
            )
            if images_data.shape[0] < 2 and not args.ignore_single_image_error:
                raise RuntimeError(
                    "The image is single sited. Was it saved in the correct way?"
                )
            if args.autotune:
                basic.autotune(images_data)
            basic.fit(images_data)
            flatfields.append(basic.flatfield)
            darkfields.append(basic.darkfield)

    # Re-arrange flatfields and darkfields axis to be (z, y, x, c)
    flatfields = np.moveaxis(np.array(flatfields), 0, -1)
    darkfields = np.moveaxis(np.array(darkfields), 0, -1)

    # Get output file names, splitext gets the file name without the extension
    flatfield_path = args.output_folder / f"{splitext(args.input.name)[0]}-ffp.tiff"
    darkfield_path = args.output_folder / f"{splitext(args.input.name)[0]}-dfp.tiff"

    # Save flatfields and darkfields
    imsave(flatfield_path, flatfields, check_contrast=False)
    imsave(darkfield_path, darkfields, check_contrast=False)


if __name__ == "__main__":
    # Import arguments
    args = get_args()

    # Run main and check time
    main(args)
