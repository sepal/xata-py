#
# Licensed to Xatabase, Inc under one or more contributor
# license agreements. See the NOTICE file distributed with
# this work for additional information regarding copyright
# ownership. Xatabase, Inc licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You
# may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

#
# Spec docs: https://xata.io/docs/rest-api/openapi
#

import datetime
import hashlib
import json
import logging
import re
import textwrap
from typing import Any, Dict

import coloredlogs
import requests
from mako.template import Template

from xata.helpers import to_rfc339

VERSION = "2.0.0"

coloredlogs.install(level="INFO")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WS_DIR = "codegen/ws"  # TODO use path from py
SCHEMA_OUT = {}
HTTP_METHODS = ["get", "put", "post", "delete", "patch"]
SPECS = {
    "core": {
        "spec_url": "https://xata.io/api/openapi?scope=core",
        "base_url": "https://api.xata.io",
    },
    "workspace": {
        "spec_url": "https://xata.io/api/openapi?scope=workspace",
        "base_url": "https://{workspaceId}.{regionId}.xata.sh",
    },
}
TYPE_REPLACEMENTS = {
    "integer": "int",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "string": "str",
}
RESERVED_WORDS = ["from"]
REF_DB_BRANCH_NAME_PARAM = "#/components/parameters/DBBranchNameParam"
REF_WORKSPACE_ID_PARAM = "#/components/parameters/WorkspaceIDParam"
REF_WORKSPACE_ID_PARAM_EXCLUSIONS = [""]
API_RENAMING = json.load(open("codegen/api-rename-mapping.json"))
DEFAULT_TEMPLATE_REF = "endpoint"

OPTIONAL_CURATED_PARAM_DB_NAME = {
    "name": "db_name",
    "in": "path",
    "schema": {"type": "string"},
    "type": "str",
    "description": "The name of the database to query. Default: database name from the client.",
    "required": False,
}
OPTIONAL_CURATED_PARAM_BRANCH_NAME = {
    "name": "branch_name",
    "in": "path",
    "schema": {"type": "string"},
    "type": "str",
    "description": "The name of the branch to query. Default: branch name from the client.",
    "required": False,
}
OPTIONAL_CURATED_PARAM_WORKSPACE_ID = {
    "name": "workspace_id",
    "in": "path",
    "schema": {"type": "string"},
    "type": "str",
    "description": "The workspace identifier. Default: workspace Id from the client.",
    "required": False,
}
OPTIONAL_CURATED_PARAM_PAYLOAD = {
    "name": "payload",
    "nameParam": "payload",
    "type": "dict",
    "description": "content",
    "in": "requestBody",
    "required": True,  # TODO get required
}


def fetch_openapi_specs(spec_url: str) -> dict:
    """
    Fetch the OpenAPI Specification and return a dict
    """
    r = requests.get(spec_url)
    logging.info("fetched the %s spec with status code: %d" % (spec_url, r.status_code))
    if r.status_code != 200:
        logging.error("could not fetch spec at: %s" % spec_url)
        exit(10)
    return r.json()


def get_class_name(name: str) -> str:
    return "".join([n.capitalize() for n in name.lower().split(" ")])


def get_param_name(name: str) -> str:
    name = "_".join([n.lower() for n in re.findall("[a-zA-Z][^A-Z]*", name)])
    if name in RESERVED_WORDS:
        name = f"{name}_"
    return name


def generate_namespace(namespace: dict, scope: str, spec_version: str, spec_base_url: str):
    """
    Generate the namespaced Class for the endpoints
    """
    if "description" in namespace:
        class_desc = namespace["description"]
    else:
        class_desc = namespace["x-displayName"]
        logging.warn("missing description: %s.%s" % (scope, namespace["x-displayName"]))
    vars = {
        "class_name": get_class_name(namespace["x-displayName"]),
        "class_description": class_desc.strip(),
        "spec_scope": scope,
        "spec_version": spec_version,
    }
    out = Template(filename="codegen/templates/namespace.tpl", output_encoding="utf-8").render(**vars)
    file_name = "%s/%s.py" % (WS_DIR, _sanitize_filename(namespace["name"]))
    fh = open(file_name, "w+")
    fh.write(out.decode("utf-8"))
    fh.close()
    logging.info("created namespace class %s in %s" % (namespace["name"], file_name))


def generate_endpoints(path: str, endpoints: dict, references: dict):
    """
    Generate the endpoints of a namespace
    """
    params = endpoints["parameters"] if "parameters" in endpoints else []
    for method in HTTP_METHODS:
        if method in endpoints:
            out = generate_endpoint(path, method, endpoints[method], params, references)
            file_name = "%s/%s.py" % (WS_DIR, _sanitize_filename(endpoints[method]["tags"][0]))
            fh = open(file_name, "a+")
            fh.write(out.decode("utf-8"))
            fh.close()
            logging.info("appended endpoint %s to %s" % (endpoints[method]["operationId"], file_name))


def prune_empty_namespaces(spec: dict) -> list[str]:
    n_spaces = {}
    for n in spec["tags"]:
        n_spaces[n["name"]] = 0
    for p in spec["paths"].values():
        for method in HTTP_METHODS:
            if method in p:
                n_spaces[p[method]["tags"][0]] += 1
    namespaces = []
    for n in spec["tags"]:
        if n_spaces[n["name"]] > 0:
            namespaces.append(n)
    return namespaces


def generate_endpoint(path: str, method: str, endpoint: dict, parameters: list, references: dict) -> str:
    """
    Generate a single endpoint
    """
    if "parameters" in endpoint:
        endpoint_params = get_endpoint_params(path, endpoint, parameters + endpoint["parameters"], references)
    else:
        endpoint_params = get_endpoint_params(path, endpoint, parameters, references)
    if "description" in endpoint:
        desc = endpoint["description"].strip()
    else:
        logging.info("missing description for %s.%s - using summary." % (path, endpoint["operationId"]))
        desc = endpoint["summary"].strip()

    # replacements
    namespace = _sanitize_filename(endpoint["tags"][0])
    operation_id = endpoint["operationId"].strip()
    template_ref = DEFAULT_TEMPLATE_REF
    if namespace in API_RENAMING and operation_id in API_RENAMING[namespace]:
        template_ref = API_RENAMING[namespace][operation_id]["template"]
        operation_id = API_RENAMING[namespace][operation_id]["name"]
        logging.debug("replacing name from %s.%s to %s." % (namespace, endpoint["operationId"].strip(), operation_id))

    # status of the API
    status = "GA"
    if "x-experimental" in endpoint:
        status = "experimental"

    # docs url
    slug = "%s#%s" % (
        re.sub("[\{\}]", "", path.strip()),
        re.sub("[ ]", "-", endpoint["summary"].lower()),
    )

    # template variables
    vars = {
        "template": template_ref,
        "operation_id": operation_id,
        "description": desc.strip("\n\r"),
        "http_method": method.upper(),
        "path": path,
        "params": endpoint_params,
        "status": status,
        "docs_url": f"https://xata.io/docs/api-reference{slug}",
    }

    SCHEMA_OUT["endpoints"].append(
        {
            "namespace": endpoint["tags"][0],
            "name": endpoint["summary"].strip(),
            "operation_id": endpoint["operationId"],
            "name_python": operation_id,
            "description": desc,
            "method": vars["http_method"],
            "url_path": path,
            "responses": endpoint_params["response_codes"],
            "status": status,
            "parameters": [
                {"name": p["name"], "description": p["description"], "in": p["in"], "required": p["required"]}
                for p in list(endpoint_params["list"])
            ],
        }
    )

    # render template
    template_path = "codegen/templates/%s.tpl" % vars["template"]
    return Template(filename=template_path, output_encoding="utf-8").render(**vars)


def get_endpoint_params(path: str, endpoint: dict, parameters: dict, references: dict) -> list:
    skel = {
        "list": [],
        "has_path_params": 0,
        "has_query_params": 0,
        "has_payload": False,
        "has_optional_params": 0,
        "smart_db_branch_name": False,
        "smart_workspace_id": False,
        "response_codes": [],
        "response_content_types": [],
    }
    if len(parameters) > 0:
        # Check for convience param swaps
        curated_param_list = []
        for r in parameters:
            if "$ref" in r and r["$ref"] == REF_DB_BRANCH_NAME_PARAM:
                logging.debug("adding smart value for %s" % "#/components/parameters/DBBranchNameParam")
                # push two new params to cover for string creation
                curated_param_list.append(OPTIONAL_CURATED_PARAM_DB_NAME)
                curated_param_list.append(OPTIONAL_CURATED_PARAM_BRANCH_NAME)
                skel["smart_db_branch_name"] = True
            elif "$ref" in r and r["$ref"] == REF_WORKSPACE_ID_PARAM:
                # and endpoint['operationId'] not in REF_WORKSPACE_ID_PARAM_EXCLUSIONS:
                logging.debug("adding smart value for %s" % "#/components/parameters/WorkspaceIdParam")
                curated_param_list.append(OPTIONAL_CURATED_PARAM_WORKSPACE_ID)
                skel["smart_workspace_id"] = True
            else:
                curated_param_list.append(r)

        for r in curated_param_list:
            p = None
            # if not in ref: endpoint specific params
            if "$ref" in r and r["$ref"] in references:
                p = references[r["$ref"]]
                if "$ref" in p["schema"]:
                    p["type"] = type_replacement(references[p["schema"]["$ref"]]["type"])
                elif "type" in p["schema"]:
                    p["type"] = type_replacement(p["schema"]["type"])
                else:
                    logging.error("could resolve type of '%s' in the lookup." % r["$ref"])
                    exit(11)
            # else if name not in r: method specific params
            elif "name" in r:
                p = r
                p["type"] = type_replacement(r["schema"]["type"])
            # else fail with code: 11
            else:
                logging.error("could resolve reference %s in the lookup." % r["$ref"])
                exit(11)

            if "required" not in p:
                p["required"] = False
            if "description" not in p:
                p["description"] = ""

            p["name"] = p["name"].strip()
            p["nameParam"] = get_param_name(p["name"])
            p["description"] = p["description"].strip()
            p["trueType"] = p["type"]
            if not p["required"]:
                p["type"] += " = None"

            skel["list"].append(p)

            if p["in"] == "path":
                skel["has_path_params"] += 1
            if p["in"] == "query":
                skel["has_query_params"] += 1
            if not p["required"]:
                skel["has_optional_params"] += 1

    if "requestBody" in endpoint:
        skel["list"].append(OPTIONAL_CURATED_PARAM_PAYLOAD)
        skel["has_payload"] = True

    # collect response schema
    if "responses" in endpoint:
        for code in endpoint["responses"]:
            desc = ""
            if "description" in endpoint["responses"][code]:
                desc = endpoint["responses"][code]["description"].strip()
            elif "$ref" in endpoint["responses"][code] and endpoint["responses"][code]["$ref"] in references:
                desc = references[endpoint["responses"][code]["$ref"]]["description"].strip()
            skel["response_codes"].append(
                {
                    "code": code,
                    "description": desc,
                }
            )
            # get content types
            if "content" in endpoint["responses"][code]:
                int_code = int(code)
                if int_code >= 200 and int_code <= 299:
                    for ct in endpoint["responses"][code]["content"]:
                        skel["response_content_types"].append({"content_type": ct, "code": code})
    # Multiple Response Content types require option for users
    if len(skel["response_content_types"]) > 1:
        skel["has_optional_params"] = True
        ct = skel["response_content_types"][0]["content_type"].lower().strip()
        skel["list"].append(
            {
                "name": "response_content_type",
                "nameParam": "response_content_type",
                "type": 'str = "%s"' % ct,
                "trueType": "str",
                "description": "Content type of the response. Default: %s" % ct,
                "in": "responseBody",
                "required": False,
            }
        )

    # Remove duplicates
    tmp = {}
    for p in skel["list"]:
        if p["name"].lower() not in tmp:
            tmp[p["name"].lower()] = p
    skel["list"] = tmp.values()

    # reorder for optional params to be last
    if skel["has_optional_params"]:
        skel["list"] = [e for e in skel["list"] if e["required"]] + [e for e in skel["list"] if not e["required"]]
    return skel


def resolve_references(spec: dict) -> dict:
    """
    Create resolvable map of all references and apply some type conversions
    """
    references = {}
    for k, group in spec["components"].items():
        for name, component in group.items():
            if "type" in component:
                component["type"] = type_replacement(component["type"])
            references[f"#/components/{k}/{name}"] = component
    return references


def type_replacement(t: str) -> str:
    orig_type = t.lower()
    for is_type, replacement in TYPE_REPLACEMENTS.items():
        if orig_type == is_type:
            return replacement
    return orig_type


def checksum(dictionary: Dict[str, Any]) -> str:
    """
    MD5 hash of a dictionary.
    credit: https://www.doc.ic.ac.uk/~nuric/coding/how-to-hash-a-dictionary-in-python.html
    """
    dhash = hashlib.md5()
    # We need to sort arguments so {'a': 1, 'b': 2} is
    # the same as {'b': 2, 'a': 1}
    encoded = json.dumps(dictionary, sort_keys=True).encode()
    dhash.update(encoded)
    return dhash.hexdigest()


def _sanitize_filename(n: str) -> str:
    return n.replace(" ", "_").lower()


# ------------------------------------------------------- #
#                         MAIN                            #
# ------------------------------------------------------- #
if __name__ == "__main__":
    for scope in SPECS.keys():
        # fetch spec
        spec = fetch_openapi_specs(SPECS[scope]["spec_url"])

        # Init schema out
        SCHEMA_OUT = {
            "scope": scope,
            "version_spec": spec["info"]["version"],
            "version_codegen": VERSION,
            "checksum": checksum(spec),
            "generated_on": to_rfc339(datetime.datetime.now(datetime.timezone.utc)),
            "base_url": SPECS[scope]["base_url"],
            "endpoints": [],
        }

        # filter out endpointless namespaces
        logging.info("pruning %d namespaces to ensure endpoints exist .." % len(spec["tags"]))
        # namespaces = spec["tags"]
        namespaces = prune_empty_namespaces(spec)

        # resolve references
        logging.info("resolving references ..")
        references = resolve_references(spec)

        # generate namespaces
        logging.info("generating %d namespaces .." % len(namespaces))
        it = 1
        for n in namespaces:
            logging.info("[%2d/%2d] creating %s" % (it, len(namespaces), n["name"]))
            generate_namespace(n, scope, spec["info"]["version"], SPECS[scope]["base_url"])
            it += 1

        # generate paths
        logging.info("generating %d paths .." % len(spec["paths"]))
        it = 1
        for path, endpoints in spec["paths"].items():
            logging.info(
                "[%2d/%2d] %s: %s" % (it, len(spec["paths"]), path, endpoints.get("summary", "MISSING-SUMMARY"))
            )
            generate_endpoints(path, endpoints, references)
            it += 1

        # fan out schema to docs
        schema_dump = open(f"codegen/ws/{scope}.json", "w")
        json.dump(SCHEMA_OUT, schema_dump, indent=2)
        schema_dump.close()
        logging.info("persisted new schema docs.")

    logging.info("done.")
