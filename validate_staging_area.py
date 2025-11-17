"""
Runs a pre-check of a staging area to identify issues that might cause the
snapshot or indexing processes to fail.
"""
import argparse
import logging
import sys


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)


from hca.staging_area_validator import StagingAreaValidator
log = logging.getLogger(__name__)

def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staging-area",
        "-s",
        required=True,
        help="The Google Cloud Storage URL of the staging area. "
        "Syntax is gs://<bucket>[/<path>].",
    )
    parser.add_argument(
        "--ignore-dangling-inputs",
        "-I",
        action="store_true",
        default=False,
        help="Ignore errors caused by metadata files not found "
        "in the staging area for input-only entities.",
    )
    parser.add_argument(
        "--no-json-validation",
        "-J",
        action="store_false",
        default=True,
        dest="validate_json",
        help="Do not validate JSON documents against their schema.",
    )
    parser.add_argument(
        "--total-retries",
        "-t",
        type=int,
        default=10,
        dest="total_retries",
        help="The number of times to retry the validation process.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output.",
    )
    parser.add_argument(
        "--lungmap",
        action="store_true",
        default=False,
        help="Use the LungMAP staging area validator.",
    )
    parser.add_argument(
        "--multiple-entities",
        action="store_true",
        default=False,
        help="Use the LungMAP staging area validator for multiple entities.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    log.info("Arguments: %s", args)
    adapter = StagingAreaValidator(
        staging_area=args.staging_area,
        ignore_dangling_inputs=args.ignore_dangling_inputs,
        validate_json=args.validate_json,
        total_retries=args.total_retries,
        verbose=args.verbose,
        lungmap=args.lungmap,
        multiple_entities=args.multiple_entities,
    )
    # if args.lungmap:
    #     adapter.multiple_entities = True
    sys.exit(adapter.main())
