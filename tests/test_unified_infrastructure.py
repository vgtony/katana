import ast
import json
import os
import time
from pathlib import Path
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]


def load_definitions(relative_path, names, globals_dict=None):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = dict(globals_dict or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class ClickStub:
    class ClickException(Exception):
        pass


class YamlStub:
    class YAMLError(Exception):
        pass

    @staticmethod
    def safe_load(stream):
        return json.load(stream)


class MongoStub:
    def __init__(self, collections=None):
        self.collections = collections or {}

    def find(self, collection, query):
        for item in self.collections.get(collection, []):
            if all(item.get(key) == value for key, value in query.items()):
                return item
        return None

    def find_all(self, collection, query=None):
        query = query or {}
        return [
            item
            for item in self.collections.get(collection, [])
            if all(item.get(key) == value for key, value in query.items())
        ]

    def add(self, collection, item):
        self.collections.setdefault(collection, []).append(item)
        return item.get("_id")

    def update(self, collection, item_id, item):
        values = self.collections.setdefault(collection, [])
        for index, current in enumerate(values):
            if current.get("_id") == item_id:
                values[index] = item
                return 1
        return 0

    def delete(self, collection, item_id):
        values = self.collections.setdefault(collection, [])
        self.collections[collection] = [
            current for current in values if current.get("_id") != item_id
        ]


class PymongoStub:
    class errors:
        class DuplicateKeyError(Exception):
            pass


class SliceConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = load_definitions(
            "katana-cli/cli/commands/cmd_slice.py",
            {"prepare_slice_data"},
            {"click": ClickStub, "yaml": YamlStub, "os": os},
        )
        cls.prepare = staticmethod(namespace["prepare_slice_data"])

    def test_existing_nest_is_unchanged(self):
        data = {"base_slice_descriptor": {}}
        self.assertIs(self.prepare(data, "/tmp/slice.yaml"), data)

    def test_relative_kubeconfig_is_loaded_for_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "creds.json"
            credential_path.write_text(
                json.dumps({"clusters": [{}], "contexts": [{}], "users": [{}]})
            )
            data = {
                "infrastructure": {
                    "type": "kubernetes",
                    "credentials_file": "creds.json",
                }
            }
            prepared = self.prepare(data, str(Path(directory) / "slice.yaml"))

        self.assertNotIn("credentials_file", prepared["infrastructure"])
        self.assertIn("credentials", prepared["infrastructure"])
        self.assertNotIn("credentials", data["infrastructure"])

    def test_openstack_requires_cloud_name_when_file_has_multiple_clouds(self):
        with tempfile.TemporaryDirectory() as directory:
            credential_path = Path(directory) / "clouds.json"
            credential_path.write_text(json.dumps({"clouds": {"one": {}, "two": {}}}))
            data = {
                "infrastructure": {
                    "type": "openstack",
                    "credentials_file": "clouds.json",
                }
            }
            with self.assertRaises(ClickStub.ClickException):
                self.prepare(data, str(Path(directory) / "slice.yaml"))


class RequestIsolationTests(unittest.TestCase):
    def test_infrastructure_is_removed_without_mutating_request(self):
        namespace = load_definitions(
            "katana-nbi/katana/api/slice.py", {"split_infrastructure"}
        )
        original = {
            "infrastructure": {"credentials": {"secret": "value"}},
            "base_slice_descriptor": {},
        }
        clean, infrastructure = namespace["split_infrastructure"](original)
        self.assertNotIn("infrastructure", clean)
        self.assertIn("infrastructure", original)
        self.assertEqual(infrastructure["credentials"]["secret"], "value")


class InfrastructureReuseTests(unittest.TestCase):
    def _check_existing(self, records):
        namespace = load_definitions(
            "katana-nbi/katana/shared_utils/infrastructureUtils.py",
            {"InfrastructureError", "_check_existing"},
            {"mongoUtils": MongoStub(records)},
        )
        return namespace["_check_existing"], namespace["InfrastructureError"]

    def test_matching_registration_is_reused(self):
        target = {
            "id": "edge-k8s",
            "type": "kubernetes",
            "location": "edge",
            "nfvo_id": "osm-1",
        }
        check, _ = self._check_existing({"k8sclusters": [target]})
        self.assertIs(check(dict(target)), target)

    def test_conflicting_registration_is_rejected(self):
        target = {
            "id": "edge-k8s",
            "type": "kubernetes",
            "location": "edge",
            "nfvo_id": "osm-1",
        }
        check, error_type = self._check_existing({"k8sclusters": [target]})
        conflicting = dict(target, nfvo_id="osm-2")
        with self.assertRaises(error_type) as raised:
            check(conflicting)
        self.assertEqual(raised.exception.status_code, 409)

    def test_missing_location_is_created(self):
        mongo = MongoStub()
        namespace = load_definitions(
            "katana-nbi/katana/shared_utils/infrastructureUtils.py",
            {"_ensure_location"},
            {"mongoUtils": mongo, "pymongo": PymongoStub, "time": time, "uuid": uuid},
        )
        location = namespace["_ensure_location"]("EDGE")
        self.assertEqual(location["id"], "edge")
        self.assertEqual(mongo.collections["location"][0]["id"], "edge")

    def test_kubernetes_credentials_are_not_persisted(self):
        class OsmStub:
            def getToken(self):
                return "token"

            def addVim(self, *args):
                return "vim-account"

            def addK8sCluster(self, payload):
                return {"id": "osm-cluster"}

        class PickleStub:
            @staticmethod
            def loads(value):
                return OsmStub()

        mongo = MongoStub(
            {
                "nfvo": [{"_id": "nfvo-db", "id": "osm-1"}],
                "nfvo_obj": [{"_id": "obj-db", "id": "osm-1", "obj": b"ignored"}],
            }
        )
        namespace = load_definitions(
            "katana-nbi/katana/shared_utils/infrastructureUtils.py",
            {"InfrastructureError", "_add_location_target", "_register_kubernetes"},
            {
                "mongoUtils": mongo,
                "pickle": PickleStub,
                "pymongo": PymongoStub,
                "time": time,
                "uuid": uuid,
            },
        )
        record = namespace["_register_kubernetes"](
            {
                "id": "edge-k8s",
                "location": "edge",
                "nfvo_id": "osm-1",
                "k8s_version": "v1.30",
                "credentials": {"clusters": [{}], "contexts": [{}], "users": [{}]},
            },
            {"_id": "location-db", "id": "edge", "vims": []},
        )
        self.assertNotIn("credentials", record)
        self.assertNotIn("credentials", mongo.collections["k8sclusters"][0])


class DescriptorClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = load_definitions(
            "katana-mngr/katana/shared_utils/nfvoUtils/osmUtils.py",
            {"classify_workload_descriptor"},
        )
        cls.classify = staticmethod(namespace["classify_workload_descriptor"])

    def test_descriptor_runtimes(self):
        self.assertEqual(self.classify({"vdu": [{}]}), "openstack")
        self.assertEqual(self.classify({"kdu": [{}]}), "kubernetes")
        self.assertEqual(self.classify({"vdu": [{}], "kdu": [{}]}), "mixed")
        self.assertEqual(self.classify({}), "unknown")


class OsmAuthenticationRetryTests(unittest.TestCase):
    class LoggerStub:
        def warning(self, *args):
            pass

    class TimeStub:
        @staticmethod
        def sleep(seconds):
            pass

    class ResponseStub:
        def __init__(self, data=None, status_code=200):
            self.data = data
            self.status_code = status_code

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class RequestsStub:
        class RequestException(Exception):
            pass

        class Timeout(RequestException):
            pass

        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = 0
            self.call_kwargs = []

        def post(self, *args, **kwargs):
            self.call_kwargs.append(kwargs)
            outcome = self.outcomes[self.calls]
            self.calls += 1
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    def _osm(self, outcomes):
        requests_stub = self.RequestsStub(outcomes)
        namespace = load_definitions(
            "katana-mngr/katana/shared_utils/nfvoUtils/osmUtils.py",
            {"OsmAuthenticationError", "OsmRequestError", "Osm"},
            {
                "requests": requests_stub,
                "time": self.TimeStub,
                "logger": self.LoggerStub(),
                "OSM_REQUEST_ATTEMPTS": 3,
                "OSM_WRITE_TIMEOUT": 30,
            },
        )
        return (
            namespace["Osm"]("osm-1", "osm.example", "user", "password"),
            requests_stub,
            namespace["OsmAuthenticationError"],
            namespace["OsmRequestError"],
        )

    def test_authentication_retries_then_succeeds(self):
        failure = self.RequestsStub.RequestException("timeout")
        osm, requests_stub, _, _ = self._osm(
            [failure, failure, self.ResponseStub({"id": "token"})]
        )

        self.assertEqual(osm.getToken(), "token")
        self.assertEqual(requests_stub.calls, 3)

    def test_authentication_failure_is_explicit_after_three_attempts(self):
        failures = [
            self.RequestsStub.RequestException("timeout") for _ in range(3)
        ]
        osm, requests_stub, error_type, _ = self._osm(failures)

        with self.assertRaises(error_type) as raised:
            osm.getToken()

        self.assertEqual(requests_stub.calls, 3)
        self.assertEqual(
            str(raised.exception), "OSM authentication failed after 3 attempts"
        )

    def test_vim_registration_reauthenticates_after_unauthorized_response(self):
        failure = self.RequestsStub.RequestException("timeout")
        osm, requests_stub, _, _ = self._osm(
            [
                self.ResponseStub(status_code=401),
                failure,
                self.ResponseStub({"id": "token"}),
                self.ResponseStub({"id": "vim-account"}),
            ]
        )
        osm.token = "stale-token"

        self.assertEqual(
            osm.addVim("name", "password", "openstack", "url", "user", {}),
            "vim-account",
        )
        self.assertEqual(requests_stub.calls, 4)
        self.assertEqual(requests_stub.call_kwargs[-1]["timeout"], 30)

    def test_vim_registration_timeout_is_explicit(self):
        osm, _, _, error_type = self._osm(
            [self.RequestsStub.Timeout("timeout")]
        )
        osm.token = "token"

        with self.assertRaises(error_type) as raised:
            osm.addVim("name", "password", "openstack", "url", "user", {})

        self.assertEqual(
            str(raised.exception),
            "OSM VIM registration timed out after 30 seconds",
        )


class SliceFailureTests(unittest.TestCase):
    class LoggerStub:
        def error(self, *args):
            pass

    def test_osm_failure_is_persisted(self):
        mongo = MongoStub({"slice": [{"_id": "slice-1"}]})
        namespace = load_definitions(
            "katana-mngr/katana/utils/sliceUtils/sliceUtils.py",
            {"fail_slice"},
            {"mongoUtils": mongo, "logger": self.LoggerStub()},
        )
        nest = {"_id": "slice-1", "status": "Provisioning"}

        namespace["fail_slice"](
            nest, "OSM authentication failed after 3 attempts"
        )

        self.assertEqual(
            nest["status"], "Failed - OSM authentication failed after 3 attempts"
        )
        self.assertEqual(
            nest["runtime_errors"]["deployment"],
            ["OSM authentication failed after 3 attempts"],
        )


class TargetResolutionTests(unittest.TestCase):
    def _resolver(self, collections):
        namespace = load_definitions(
            "katana-mngr/katana/utils/sliceUtils/sliceUtils.py",
            {"_deployment_target", "resolve_deployment_target"},
            {"mongoUtils": MongoStub(collections)},
        )
        return namespace["resolve_deployment_target"]

    def _ns(self, runtime="kubernetes"):
        return {"nsd-info": {"deployment_runtime": runtime, "nfvo_id": "osm-1"}}

    def test_single_compatible_target_is_selected(self):
        target = {
            "id": "edge-k8s",
            "type": "kubernetes",
            "location": "edge",
            "nfvo_id": "osm-1",
        }
        selected, error = self._resolver({"k8sclusters": [target]})(self._ns(), "edge")
        self.assertIs(selected, target)
        self.assertIsNone(error)

    def test_ambiguous_targets_fail(self):
        targets = [
            {"id": name, "type": "kubernetes", "location": "edge", "nfvo_id": "osm-1"}
            for name in ("one", "two")
        ]
        selected, error = self._resolver({"k8sclusters": targets})(self._ns(), "edge")
        self.assertIsNone(selected)
        self.assertIn("Multiple kubernetes", error)

    def test_missing_target_fails_explicitly(self):
        selected, error = self._resolver({})(self._ns(), "edge")
        self.assertIsNone(selected)
        self.assertIn("No kubernetes infrastructure", error)

    def test_preferred_target_wins(self):
        targets = [
            {"id": name, "type": "kubernetes", "location": "edge", "nfvo_id": "osm-1"}
            for name in ("one", "two")
        ]
        selected, error = self._resolver({"k8sclusters": targets})(
            self._ns(), "edge", preferred_target_id="two"
        )
        self.assertEqual(selected["id"], "two")
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
