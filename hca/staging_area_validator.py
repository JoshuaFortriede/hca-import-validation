import base64
import json
import logging
import uuid
from functools import cached_property, lru_cache
from typing import MutableMapping, MutableSequence, Optional, Tuple, TypeVar
from urllib import parse

import google.cloud.storage as gcs
import requests
from jsonschema import FormatChecker, validate
from more_itertools import one
from requests.adapters import HTTPAdapter, Retry

T = TypeVar("T")
JSON = MutableMapping[str, T]

log = logging.getLogger(__name__)

staging_area_properties_schema = {
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "properties": {"is_delta": {"type": "boolean"}},
    "required": ["is_delta"],
    "additionalProperties": False,
}

class GCP:
    def __init__(self, staging_area: str, verbose, lungmap) -> None:
        self.verbose = verbose
        self.lungmap = lungmap
        self.client = gcs.Client()
        self.bucket, self.sa_path = self._parse_gcs_url(staging_area)

    # def generate_sa_path()

    def parse_sa_path(self, staging_area: str) -> str:
        """
        Parse the staging area path from the GCS URL
        """
        self.bucket, self.sa_path = self._parse_gcs_url(staging_area)

    def _parse_gcs_url(self, gcs_url: str) -> Tuple[gcs.Bucket, str]:
        """
        Parse a GCS URL into its Bucket and path components
        """
        split_url = parse.urlsplit(gcs_url)
        if split_url.scheme != "gs" or not split_url.netloc:
            print(
                "Error: Google Cloud Storage URL must be in gs://<bucket>[/<path>] format"
            )
            exit(1)
        if split_url.path.endswith("/"):
            print('Error: Google Cloud Storage URL must not end with a "/"')
            exit(1)
        if split_url.path:
            path = split_url.path.lstrip("/") + "/"
        else:
            path = ""
        bucket = gcs.Bucket(self.client, split_url.netloc)
        return bucket, path

    def get_staging_area_json(self, verbose: bool, lungmap: bool) -> JSON:
        """
        Get the staging area JSON file from GCS.
        """
        if lungmap:
            log.warning(
                "LungMAP staging area validator does not support delta staging areas"
            )
            return {'is_delta': False}

        properties_file_path = self.sa_path + "staging_area.json"
        blob = self.bucket.get_blob(properties_file_path)
        assert isinstance(blob, gcs.Blob), properties_file_path
        return json.loads(self.download_blob_as_json(blob))

    def download_blob_as_json(self, blob: gcs.Blob) -> Optional[JSON]:
        file_json = json.loads(blob.download_as_bytes())
        return file_json
    
    def get_files_in_path(self, path: str) -> MutableSequence[gcs.Blob]:
        """
        Get all files in a given path in the GCS bucket.
        """
        blobs = self.bucket.list_blobs(prefix=f"{self.sa_path}{path}")
        return_blobs = []
        for blob in blobs:
            _, _, file_name = blob.name.rpartition("/")
            # assert file_name.count("_") == 2
            # assert file_name.endswith(".json")
            return_blobs.append(blob)

        return return_blobs
    
    def get_filename(self, blob: gcs.Blob, full: bool = False) -> str:
        """
        Get the file name from a GCS blob.
        """
        if full:
            return blob.name
        _, _, file_name = blob.name.rpartition("/")
        return file_name

    def get_crc32c(self, blob: gcs.Blob) -> str:
        """
        Get the CRC32C checksum of a GCS blob.
        """
        return base64.b64decode(blob.crc32c).hex()

    def get_submission_paths(self) -> str:
        """
        Get the list of submissions
        """
        folders = set()
        # print(self.bucket.name)
        for blob in self.bucket.list_blobs(prefix=self.sa_path):
            parts = blob.name.split('/')
            if len(parts) > 1:
                # print(parts[0], parts[1])
                folders.add(f"gs://{self.bucket.name}/{parts[0]}/{parts[1]}")

        return folders

class StagingAreaValidator:
    # pylint: disable=too-many-instance-attributes
    def main(self):
        base_staging_area = self.staging_area
        print("Base SA",base_staging_area)
        print("Base PATH",self.client.sa_path)

        if self.multiple_entities:
            log.info("Multiple entities validation is enabled.")
            # print(self.client.get_submission_paths())
            staging_areas = list(self.client.get_submission_paths())
            print("Found {} submissions".format(len(staging_areas)))
            # print("SA",staging_areas)
            # return 0

        else:
            staging_areas = [self.staging_area]

        for staging_area in staging_areas:
            self.extra_files.clear()
            self.file_errors.clear()
            self.file_error_summary.clear()
            self.names_to_id.clear()
            self.metadata_files.clear()

            print(f"Validating staging area: {staging_area}")

            self.client.parse_sa_path(staging_area)
            # self.staging_area = staging_area
            # self.client.sa_path = staging_area
            # print("New  SA",self.staging_area)
            # print("NEW  Path",self.client.sa_path)

            self._run()
            exit_code = 0
            # pylint: disable=invalid-name
            # for file_name, e in self.file_errors.items():
                # log.error("Error with file: %s", file_name, exc_info=e)
            for file_name in self.extra_files:
                log.warning("File is not part of a subgraph: %s", file_name)
            if self.file_errors:
                exit_code |= 1
                for schema, files in self.file_error_summary.items():
                    log.error("Encountered %i files with errors in %s", len(files), schema)
            
        return exit_code

    date_format = "%Y-%m-%dT%H:%M:%S.%fZ"

    def __init__(
        self,
        staging_area: str,
        ignore_dangling_inputs: bool,
        validate_json: bool,
        total_retries=10,
        verbose: bool = False,
        lungmap: bool = False,
        multiple_entities: bool = False,
    ) -> None:
        super().__init__()
        self.staging_area = staging_area
        self.validate_json = validate_json
        self.ignore_dangling_inputs = ignore_dangling_inputs
        self.file_error_summary={}
        self.verbose = verbose
        self.lungmap = lungmap
        self.multiple_entities = multiple_entities

        if staging_area.startswith("gs://"):
            # Use the GCP client to get the staging area JSON file
            self.client = GCP(staging_area, self.verbose, self.lungmap)

        # self.gcs = gcs.Client()

        # Number of retries for validation
        self.total_retries = total_retries
        # A boolean to tell us if this is a delta or non-delta staging area
        self.is_delta = None
        # A mapping of data file name to metadata id
        self.names_to_id: MutableMapping[str, str] = {}
        # The status of each metadata file checked
        self.metadata_files: MutableMapping[str, JSON] = {}
        # A mapping of file name to validation error
        self.file_errors: MutableMapping[str, Exception] = {}
        # Any files found that are not part of a subgraph link
        self.extra_files: MutableSequence[str] = []
        # self.bucket, self.sa_path = self._parse_gcs_url(self.staging_area)

    @cached_property
    def validator(self):
        return SchemaValidator()

    def _parse_gcs_url(self, gcs_url: str) -> Tuple[gcs.Bucket, str]:
        """
        Parse a GCS URL into its Bucket and path components
        """
        split_url = parse.urlsplit(gcs_url)
        if split_url.scheme != "gs" or not split_url.netloc:
            print(
                "Error: Google Cloud Storage URL must be in gs://<bucket>[/<path>] format"
            )
            exit(1)
        if split_url.path.endswith("/"):
            print('Error: Google Cloud Storage URL must not end with a "/"')
            exit(1)
        if split_url.path:
            path = split_url.path.lstrip("/") + "/"
        else:
            path = ""
        bucket = gcs.Bucket(self.gcs, split_url.netloc)
        return bucket, path

    def _run(self):
        self.file_errors.clear()
        self.validate_staging_area_properties()
        if not self.file_errors:
            self.validate_files("links")
            self.validate_files("metadata")
            self.validate_files("descriptors")
            self.validate_files("data")
            self.check_results()

    #Done
    def validate_staging_area_properties(self) -> None:
        """
        Verify and parse the staging area properties file.
        """
        print("Checking staging area properties")

        file_json = self.client.get_staging_area_json(self.verbose, self.lungmap)

        self.validate_file_json(file_json, 'staging_area.json', staging_area_properties_schema)
        self.is_delta = file_json["is_delta"]
        assert isinstance(self.is_delta, bool), file_json

    def validate_files(self, path: str) -> None:
        print(f"Checking files in {self.client.sa_path}{path}")
        validate_file_fn = getattr(self, f"validate_{path}_file")
        for blob in self.client.get_files_in_path(path):
            try:
                validate_file_fn(blob)
            except KeyboardInterrupt:
                exit()
            except Exception as e:
                log.error("File error: %s", self.client.get_filename(blob))
                self.file_errors[self.client.get_filename(blob)] = e

    def download_blob_as_json(self, blob: gcs.Blob) -> Optional[JSON]:
        file_json = json.loads(blob.download_as_bytes())
        return file_json

    # def validate_links_file(self, blob: gcs.Blob) -> None:
    def validate_links_file(self, blob) -> None:
        # Expected syntax: links/{bundle_uuid}_{version}_{project_uuid}.json
        file_name = self.client.get_filename(blob)
        # _, _, file_name = blob.name.rpartition("/")
        assert file_name.count("_") == 2
        assert file_name.endswith(".json")
        _, _, project_uuid = file_name[:-5].split("_")
        # file_json = self.download_blob_as_json(blob)
        file_json = self.client.download_blob_as_json(blob)
        self.validate_file_json(file_json, file_name)
        for link in file_json["links"]:
            link_type = link["link_type"]
            if link_type == "process_link":
                self.add_metadata_file(
                    entity_id=link["process_id"],
                    entity_type=link["process_type"],
                    project_uuid=project_uuid,
                    category="process",
                )
                for category in ("input", "output", "protocol"):
                    for entity in link[f"{category}s"]:
                        entity_type = entity[f"{category}_type"]
                        entity_id = entity[f"{category}_id"]
                        self.add_metadata_file(
                            entity_id=entity_id,
                            entity_type=entity_type,
                            project_uuid=project_uuid,
                            category=category,
                        )
            elif link_type == "supplementary_file_link":
                assert link["entity"]["entity_type"] == "project", link["entity"]
                assert link["entity"]["entity_id"] == project_uuid, link["entity"]
                for entity in link["files"]:
                    entity_type = entity["file_type"]
                    entity_id = entity["file_id"]
                    self.add_metadata_file(
                        entity_id=entity_id,
                        entity_type=entity_type,
                        project_uuid=project_uuid,
                        category="supplementary",
                    )
        if project_uuid not in self.metadata_files:
            self.add_metadata_file(
                entity_id=project_uuid,
                entity_type="project",
                project_uuid=project_uuid,
                category="project",
            )

    def add_metadata_file(
        self, entity_id: str, entity_type: str, project_uuid: str, category: str
    ) -> None:
        try:
            file = self.metadata_files[entity_id]
        except KeyError:
            self.metadata_files[entity_id] = {
                "name": set(),
                "entity_id": entity_id,
                "entity_type": entity_type,
                "metadata_versions": set(),
                "descriptor_versions": set(),
                "project": {project_uuid},
                "category": {category},
                "found_metadata": False,
            }
        else:
            file["project"].add(project_uuid)
            file["category"].add(category)
        assert self.metadata_files[entity_id]["entity_type"] == entity_type

    def validate_metadata_file(self, blob) -> None:
        # Expected syntax: metadata/{metadata_type}/{metadata_id}_{version}.json
        blob_name = self.client.get_filename(blob, full=True)
        metadata_type, metadata_file = blob_name.split("/")[-2:]
        assert metadata_file.count("_") == 1
        assert metadata_file.endswith(".json")
        metadata_id, metadata_version = metadata_file[:-5].split("_")
        file_json = self.client.download_blob_as_json(blob)
        self.validate_file_json(file_json, blob_name)
        provenance = file_json["provenance"]
        assert metadata_id == provenance["document_id"]

        if metadata_file := self.metadata_files.get(metadata_id):
            metadata_file["name"].add(blob_name)
            metadata_file["metadata_versions"].add(metadata_version)
            metadata_file["found_metadata"] = True
            if metadata_type.endswith("_file"):
                metadata_file["data_file_name"] = file_json["file_core"]["file_name"]
                metadata_file["found_data_file"] = False

            if metadata_type == "supplementary_file" and "submitter_id" in provenance:
                content_description = file_json["file_core"].get(
                    "content_description", []
                )
                if any(
                    "matrix" in v.lower()
                    for cd in content_description
                    for v in cd.values()
                ):
                    try:
                        self.validate_stratification(file_json.get("file_description"))
                    except Exception as e:
                        self.file_errors[blob.name] = e
                        metadata_file["valid_stratification"] = False
                        log.error("Invalid file_description in %s.", blob.name)
                    else:
                        metadata_file["valid_stratification"] = True

            # if metadata_type == "supplementary_file" and file_json.get(
            #     "provenance", {}
            # ).get("submitter_id"):
            #     try:
            #         self.validate_file_description(file_json.get("file_description"))
            #     except Exception as e:
            #         self.file_errors[blob_name] = e
            #         metadata_file["valid_stratification"] = False
            #         log.error("Invalid file_description in %s.", blob_name)
            #     else:
            #         metadata_file["valid_stratification"] = True
        else:
            self.extra_files.append(blob_name)

    def validate_stratification(self, stratification: str) -> None:
        if not stratification:
            return
        strata = [
            {
                dimension: values.split(",")
                for dimension, values in (
                    point.split("=") for point in stratum.split(";")
                )
            }
            for stratum in stratification.split("\n")
        ]
        log.debug("Strata: %s", strata)
        valid_keys = [
            "genusSpecies",
            "developmentStage",
            "organ",
            "libraryConstructionApproach",
        ]
        for stratum in strata:
            for dimension, values in stratum.items():
                assert dimension in valid_keys, stratum
                assert len(values) > 0, stratum

    def validate_descriptors_file(self, blob) -> None:
        # Expected syntax: descriptors/{metadata_type}/{metadata_id}_{version}.json
        blob_name = self.client.get_filename(blob, full=True)
        descriptor_file = blob_name.split("/")[-1]
        assert descriptor_file.count("_") == 1
        assert descriptor_file.endswith(".json")

        metadata_id, descriptor_version = descriptor_file[:-5].split("_")
        # file_json = self.download_blob_as_json(blob)
        file_json = self.client.download_blob_as_json(blob)
        self.validate_file_json(file_json, blob_name)
        file_name = file_json["file_name"]
        self.names_to_id[file_name] = metadata_id

        if metadata_file := self.metadata_files.get(metadata_id):
            metadata_file["crc32c"] = file_json["crc32c"]
            metadata_versions = metadata_file["metadata_versions"]
            if metadata_file["entity_type"] == "sequence_file":
                if "drs_uri" in file_json:
                    metadata_file["found_data_file"] = True

            assert (
                descriptor_version in metadata_versions
            ), f"Corresponding metadata version for descriptor version {descriptor_version} not found"
            metadata_file["descriptor_versions"].add(descriptor_version)
        else:
            self.extra_files.append(blob_name)

    def validate_data_file(self, blob) -> None:
        # Expected syntax: data/{file_path}
        file_name = self.client.get_filename(blob, full=True).split('/data/',1)[1]
        # file_name = blob.name[len(prefix) :]
        if self.verbose:
            print("Validating data file", file_name)
        metadata_file = {}
        if metadata_id := self.names_to_id.get(file_name):
            if metadata_file := self.metadata_files.get(metadata_id):
                metadata_file["found_data_file"] = True
                assert metadata_file["crc32c"] == self.client.get_crc32c(blob)
        if metadata_file is None:
            self.extra_files.append(self.client.get_filename(blob))

    def validate_file_json(
        self, file_json: JSON, file_name: str, schema: Optional[JSON] = None
    ) -> None:
        if self.validate_json:
            # print(f"Validating JSON of {file_name}       ", end='\r', flush=True)
            if self.verbose:
                print(f"Validating JSON of {file_name}", flush=True)
            try:
                self.validator.validate_json(file_json, self.total_retries, schema)
            except Exception as e:
                log.error("File %s failed json validation.", file_name)
                self.file_errors[file_name] = e

    def check_results(self):
        print("Checking results")

        for metadata_id, metadata_file in self.metadata_files.items():
            try:
                self.check_result(metadata_file)
            except Exception as e:
                log.error("File error: %s", metadata_file)
                if metadata_file['entity_type'] not in self.file_error_summary:
                    self.file_error_summary[metadata_file['entity_type']] = []
                self.file_error_summary[metadata_file['entity_type']].append(metadata_file)
                self.file_errors[metadata_id] = e
        if not self.file_errors and not self.extra_files:
            print("No errors found")

    def check_result(self, metadata_file):
        if self.ignore_dangling_inputs and metadata_file["category"] == {"input"}:
            pass
        else:
            if not metadata_file["found_metadata"]:
                if metadata_file["entity_type"] == "project":
                    log.warning(
                        "A metadata file was not found for project %s",
                        one(metadata_file["project"]),
                    )
                else:
                    raise Exception("Did not find metadata file", metadata_file)
            if self.is_delta and len(metadata_file["metadata_versions"]) > 1:
                raise Exception(
                    "Delta staging areas must not contain redundant "
                    "versions of metadata.",
                    metadata_file,
                )
            if metadata_file["entity_type"].endswith("_file"):
                if (
                    not metadata_file["descriptor_versions"]
                    == metadata_file["metadata_versions"]
                ):
                    raise Exception(
                        "Did not find a matching descriptor file", metadata_file
                    )
                ## found_data_file set default to false when adding metadata_file
                ## found_data_file set to true when validating data file
                ## If sequence_file, and there is a DRS URI or its null, then we should set to true.
                if not metadata_file["found_data_file"]:
                    print("\t",metadata_file["entity_type"], metadata_file['data_file_name'])
                    raise Exception("Did not find data file", metadata_file)
        try:
            stratification = metadata_file["valid_stratification"]
        except KeyError:
            pass
        else:
            if not stratification:
                raise Exception(
                    "File has a invalid stratification value", metadata_file
                )
            else:
                pass

    def validate_uuid(self, value: str) -> None:
        """
        Verify given value is a valid UUID string.
        """
        try:
            uuid.UUID(value)
        except ValueError as e:
            raise ValueError("Invalid uuid value", value) from e


class SchemaValidator:
    @classmethod
    def validate_json(
        cls,
        file_json: JSON,
        total_retries: int,
        schema: Optional[JSON] = None,
    ) -> None:
        if schema is None:
            try:
                schema = cls._download_schema(file_json["describedBy"], total_retries)
            except json.decoder.JSONDecodeError as e:
                schema_url = file_json["describedBy"]
                raise Exception("Failed to parse schema JSON", schema_url) from e
        validate(file_json, schema, format_checker=FormatChecker())

    @classmethod
    # setting to maxsize=None so as not to evict old values, and maybe help avoid connectivity issues (DI-22)
    @lru_cache(maxsize=None)
    def _download_schema(cls, schema_url: str, total_retries: int) -> JSON:
        log.debug("Downloading schema %s", schema_url)

        s = requests.Session()
        log.debug(f"total_retries = {total_retries}")
        retries = Retry(
            total=total_retries,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504],
        )
        s.mount("http://", HTTPAdapter(max_retries=retries))
        s.mount("https://", HTTPAdapter(max_retries=retries))

        response = s.get(schema_url, allow_redirects=False)
        response.raise_for_status()
        return response.json()
