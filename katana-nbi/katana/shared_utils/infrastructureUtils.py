import pickle
import time
import uuid

from bson.binary import Binary
import pymongo

from katana.shared_utils.mongoUtils import mongoUtils
from katana.shared_utils.vimUtils import openstackUtils


SUPPORTED_TYPES = {"openstack", "kubernetes"}


class InfrastructureError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _required(data, fields):
    missing = [field for field in fields if not data.get(field)]
    if missing:
        raise InfrastructureError(
            "Error: Required infrastructure fields: " + ", ".join(missing)
        )


def _ensure_location(location_id):
    location_id = location_id.lower()
    location = mongoUtils.find("location", {"id": location_id})
    if location:
        return location

    location = {
        "_id": str(uuid.uuid4()),
        "id": location_id,
        "created_at": time.time(),
        "description": f"Automatically created for infrastructure at {location_id}",
        "vims": [],
        "functions": [],
    }
    try:
        mongoUtils.add("location", location)
    except pymongo.errors.DuplicateKeyError:
        location = mongoUtils.find("location", {"id": location_id})
    return location


def _check_existing(data):
    infrastructure_id = data["id"]
    existing = mongoUtils.find("vim", {"id": infrastructure_id})
    if not existing:
        existing = mongoUtils.find("k8sclusters", {"id": infrastructure_id})
    if not existing:
        return None

    expected = {
        "type": data["type"],
        "location": data["location"].lower(),
        "nfvo_id": data["nfvo_id"],
    }
    actual = {
        "type": existing.get("type"),
        "location": existing.get("location"),
        "nfvo_id": existing.get("nfvo_id"),
    }
    if actual != expected:
        raise InfrastructureError(
            f"Infrastructure {infrastructure_id} is already registered with different settings",
            409,
        )
    return existing


def _openstack_auth(data):
    credentials = data.get("credentials")
    if not isinstance(credentials, dict):
        raise InfrastructureError("OpenStack credentials must be a YAML object")

    if "clouds" in credentials:
        clouds = credentials.get("clouds")
        if not isinstance(clouds, dict) or not clouds:
            raise InfrastructureError("OpenStack credentials contain no clouds")
        cloud_name = data.get("cloud")
        if not cloud_name:
            if len(clouds) != 1:
                raise InfrastructureError(
                    "Field infrastructure.cloud is required when clouds.yaml has multiple clouds"
                )
            cloud_name = next(iter(clouds))
        cloud = clouds.get(cloud_name)
        if not isinstance(cloud, dict):
            raise InfrastructureError(f"OpenStack cloud {cloud_name} was not found")
        auth = cloud.get("auth") or {}
    else:
        auth = credentials.get("auth") or credentials

    normalized = {
        "auth_url": auth.get("auth_url"),
        "username": auth.get("username"),
        "password": auth.get("password"),
        "admin_project_name": auth.get("project_name")
        or auth.get("project_id")
        or auth.get("tenant_name"),
    }
    _required(normalized, ("auth_url", "username", "password", "admin_project_name"))
    return normalized


def _add_location_target(location, infrastructure_id):
    if infrastructure_id not in location["vims"]:
        location["vims"].append(infrastructure_id)
        mongoUtils.update("location", location["_id"], location)


def _register_openstack(data, location):
    auth = _openstack_auth(data)
    db_id = str(uuid.uuid4())
    try:
        target = openstackUtils.Openstack(
            uuid=db_id,
            auth_url=auth["auth_url"],
            project_name=auth["admin_project_name"],
            username=auth["username"],
            password=auth["password"],
            verify=False,
        )
        if target.auth_error:
            raise InfrastructureError("Unable to authenticate with OpenStack", 502)
        resources = target.get_resources()
    except InfrastructureError:
        raise
    except Exception:
        raise InfrastructureError("OpenStack registration failed", 502)

    record = {
        "_id": db_id,
        "id": data["id"],
        "type": "openstack",
        "location": data["location"].lower(),
        "nfvo_id": data["nfvo_id"],
        "created_at": time.time(),
        "tenants": {},
        "resources": resources,
        **auth,
    }
    try:
        mongoUtils.add("vim", record)
        mongoUtils.add(
            "vim_obj",
            {"_id": db_id, "id": data["id"], "obj": Binary(pickle.dumps(target))},
        )
    except pymongo.errors.DuplicateKeyError:
        mongoUtils.delete("vim", db_id)
        raise InfrastructureError(f"Infrastructure {data['id']} already exists", 409)
    except Exception:
        mongoUtils.delete("vim", db_id)
        raise InfrastructureError("Unable to store OpenStack infrastructure", 500)
    _add_location_target(location, data["id"])
    return record


def _register_kubernetes(data, location):
    credentials = data.get("credentials")
    if not isinstance(credentials, dict):
        raise InfrastructureError("Kubernetes credentials must be a kubeconfig object")
    for field in ("clusters", "contexts", "users"):
        if not credentials.get(field):
            raise InfrastructureError(f"Kubernetes kubeconfig field {field} is missing")

    nfvo = mongoUtils.find("nfvo", {"id": data["nfvo_id"]})
    nfvo_obj_record = mongoUtils.find("nfvo_obj", {"id": data["nfvo_id"]})
    if not nfvo or not nfvo_obj_record:
        raise InfrastructureError(f"NFVO {data['nfvo_id']} is not registered", 400)
    osm = pickle.loads(nfvo_obj_record["obj"])
    backing_name = f"katana-k8s-{data['id']}"
    try:
        osm.getToken()
        vim_account = osm.addVim(
            backing_name,
            "unused",
            "dummy",
            "http://unused.invalid",
            "unused",
            {},
        )
    except Exception:
        raise InfrastructureError("OSM Kubernetes authentication failed", 502)
    if not vim_account:
        raise InfrastructureError("OSM failed to create the Kubernetes backing account", 502)

    payload = {
        "name": data["id"],
        "credentials": credentials,
        "vim_account": vim_account,
        "k8s_version": data["k8s_version"],
        "nets": data.get("nets", {}),
        "namespace": data.get("namespace", "default"),
        "deployment_methods": data.get(
            "deployment_methods", {"helm-chart-v3": True}
        ),
    }
    try:
        osm_response = osm.addK8sCluster(payload)
    except Exception:
        try:
            osm.deleteVim(vim_account)
        except Exception:
            pass
        raise InfrastructureError("OSM failed to register Kubernetes", 502)

    record = {
        "_id": str(uuid.uuid4()),
        "id": data["id"],
        "name": data["id"],
        "type": "kubernetes",
        "location": data["location"].lower(),
        "nfvo_id": data["nfvo_id"],
        "created_at": time.time(),
        "vim_account": vim_account,
        "osm_id": osm_response.get("_id") or osm_response.get("id"),
        "k8s_version": data["k8s_version"],
        "namespace": payload["namespace"],
        "nets": payload["nets"],
        "deployment_methods": payload["deployment_methods"],
    }
    try:
        mongoUtils.add("k8sclusters", record)
    except Exception as exc:
        try:
            if record["osm_id"]:
                osm.deleteK8sCluster(record["osm_id"])
            osm.deleteVim(vim_account)
        except Exception:
            pass
        if isinstance(exc, pymongo.errors.DuplicateKeyError):
            raise InfrastructureError(f"Infrastructure {data['id']} already exists", 409)
        raise InfrastructureError("Unable to store Kubernetes infrastructure", 500)
    _add_location_target(location, data["id"])
    return record


def ensure_infrastructure(data):
    if not isinstance(data, dict):
        raise InfrastructureError("Field infrastructure must be a YAML object")
    _required(data, ("id", "type", "location", "nfvo_id"))
    data = dict(data)
    data["type"] = data["type"].lower()
    data["location"] = data["location"].lower()
    if data["type"] not in SUPPORTED_TYPES:
        raise InfrastructureError(
            "Infrastructure type must be openstack or kubernetes"
        )
    if data["type"] == "kubernetes" and not data.get("k8s_version"):
        raise InfrastructureError("Infrastructure field k8s_version is required")

    if not mongoUtils.find("nfvo", {"id": data["nfvo_id"]}):
        raise InfrastructureError(f"NFVO {data['nfvo_id']} is not registered")

    existing = _check_existing(data)
    if existing:
        return existing, False

    location = _ensure_location(data["location"])
    if data["type"] == "openstack":
        return _register_openstack(data, location), True
    return _register_kubernetes(data, location), True
