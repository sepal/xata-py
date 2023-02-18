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

# ------------------------------------------------------- #
# Authentication
# Authentication and API Key management.
# Specification: core:v1.0
# Base URL: https://api.xata.io
# ------------------------------------------------------- #

from requests import Response

from xata.namespace import Namespace


class Authentication(Namespace):

    base_url = "https://api.xata.io"
    scope = "core"

    def getUserAPIKeys(
        self,
    ) -> Response:
        """
        Retrieve a list of existing user API keys
        Path: /user/keys
        Method: GET


        :return Response
        """
        url_path = "/user/keys"
        return self.request("GET", url_path)

    def createUserAPIKey(self, key_name: str) -> Response:
        """
        Create and return new API key
        Path: /user/keys/{key_name}
        Method: POST

        :param key_name: str API Key name

        :return Response
        """
        url_path = f"/user/keys/{key_name}"
        return self.request("POST", url_path)

    def deleteUserAPIKey(self, key_name: str) -> Response:
        """
        Delete an existing API key
        Path: /user/keys/{key_name}
        Method: DELETE

        :param key_name: str API Key name

        :return Response
        """
        url_path = f"/user/keys/{key_name}"
        return self.request("DELETE", url_path)