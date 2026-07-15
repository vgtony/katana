import requests
import json
import os
import yaml

import click
import datetime


def prepare_slice_data(data, slice_file):
    """Resolve an optional infrastructure credential file for API transport."""
    if not isinstance(data, dict):
        raise click.ClickException("Slice file must contain a YAML object")

    infrastructure = data.get("infrastructure")
    if infrastructure is None:
        return data
    if not isinstance(infrastructure, dict):
        raise click.ClickException("Field infrastructure must be a YAML object")

    prepared = dict(data)
    infrastructure = dict(infrastructure)
    prepared["infrastructure"] = infrastructure
    credentials_file = infrastructure.pop("credentials_file", None)
    if not credentials_file:
        return prepared

    credentials_path = os.path.join(os.path.dirname(os.path.abspath(slice_file)), credentials_file)
    try:
        with open(credentials_path, mode="r") as stream:
            credentials = yaml.safe_load(stream)
    except FileNotFoundError:
        raise click.ClickException(f"Credentials file {credentials_file} not found")
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Error parsing credentials file: {exc}")

    if not isinstance(credentials, dict):
        raise click.ClickException("Credentials file must contain a YAML object")
    infrastructure_type = str(infrastructure.get("type", "")).lower()
    if infrastructure_type == "kubernetes":
        missing = [key for key in ("clusters", "contexts", "users") if not credentials.get(key)]
        if missing:
            raise click.ClickException(
                "Invalid kubeconfig; missing: " + ", ".join(missing)
            )
    elif infrastructure_type == "openstack":
        clouds = credentials.get("clouds")
        if not isinstance(clouds, dict) or not clouds:
            raise click.ClickException("OpenStack credentials must be a clouds.yaml file")
        cloud_name = infrastructure.get("cloud")
        if cloud_name and cloud_name not in clouds:
            raise click.ClickException(f"OpenStack cloud {cloud_name} was not found")
        if not cloud_name and len(clouds) != 1:
            raise click.ClickException(
                "Field infrastructure.cloud is required when clouds.yaml has multiple clouds"
            )
    else:
        raise click.ClickException("Infrastructure type must be openstack or kubernetes")

    infrastructure["credentials"] = credentials
    return prepared


@click.group()
def cli():
    """Manage slices"""
    pass


@click.command()
def ls():
    """
    List slices
    """

    url = "http://localhost:8000/api/slice"
    r = None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        json_data = json.loads(r.content)
        print(console_formatter("SLICE_ID", "SLICE_NAME", "CREATED AT", "STATUS"))
        for i in range(len(json_data)):
            print(console_formatter(json_data[i]["_id"], json_data[i]["name"], datetime.datetime.fromtimestamp(json_data[i]["created_at"]).strftime("%Y-%m-%d %H:%M:%S"), json_data[i]["status"],))

    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


@click.command()
@click.argument("uuid")
def inspect(uuid):
    """
    Display detailed information of slice
    """
    url = "http://localhost:8000/api/slice/" + uuid
    r = None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        json_data = json.loads(r.content)
        click.echo(json.dumps(json_data, indent=2))
        if not json_data:
            click.echo(f"Error: No such slice: {uuid}")
    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


@click.command()
@click.argument("uuid")
def deployment_time(uuid):
    """
    Display deployment slice of slice
    """
    url = "http://localhost:8000/api/slice/{0}/time".format(uuid)
    r = None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        json_data = json.loads(r.content)
        click.echo(json.dumps(json_data, indent=2))
        if not json_data:
            click.echo("Error: No such slice: {}".format(uuid))
    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


@click.command()
@click.argument("uuid")
def errors(uuid):
    """
    Display errors of slice
    """
    url = "http://localhost:8000/api/slice/{0}/errors".format(uuid)
    r = None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        json_data = json.loads(r.content)
        click.echo(json.dumps(json_data, indent=2))
        if not json_data:
            click.echo("Error: No such slice: {}".format(uuid))
    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


@click.command()
@click.option("-f", "--file", required=True, type=str, help="yaml file with slice details")
def add(file):
    """
    Add new slice
    """
    try:
        stream = open(file, mode="r")
    except FileNotFoundError:
        raise click.ClickException(f"File {file} not found")

    with stream:
        data = yaml.safe_load(stream)
    data = prepare_slice_data(data, file)

    url = "http://localhost:8000/api/slice"
    r = None
    try:
        r = requests.post(url, json=json.loads(json.dumps(data)), timeout=30)
        r.raise_for_status()

        click.echo(r.content)
    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


@click.command()
@click.argument("id_list", nargs=-1)
@click.option("--force", required=False, default=False, is_flag=True, help="Force delete a slice")
def rm(id_list, force):
    """
    Remove slices
    """
    for _id in id_list:

        force_arg = "?force=true" if force else ""

        url = "http://localhost:8000/api/slice/" + _id + force_arg
        r = None
        try:
            r = requests.delete(url, timeout=30)
            r.raise_for_status()
            click.echo(r.content)
        except requests.exceptions.HTTPError as errh:
            print("Http Error:", errh)
            click.echo(r.content)
        except requests.exceptions.ConnectionError as errc:
            print("Error Connecting:", errc)
        except requests.exceptions.Timeout as errt:
            print("Timeout Error:", errt)
        except requests.exceptions.RequestException as err:
            print("Error:", err)


@click.command()
@click.option("-f", "--file", required=True, type=str, help="yaml file with slice details")
@click.argument("id")
def modify(file, id):
    """
    Update slice
    """
    try:
        stream = open(file, mode="r")
    except FileNotFoundError:
        raise click.ClickException(f"File {file} not found")

    with stream:
        data = yaml.safe_load(stream)

    url = "http://localhost:8000/api/slice/" + id + "/modify"
    r = None
    try:
        r = requests.post(url, json=json.loads(json.dumps(data)), timeout=30)
        r.raise_for_status()

        click.echo(r.content)
    except requests.exceptions.HTTPError as errh:
        print("Http Error:", errh)
        click.echo(r.content)
    except requests.exceptions.ConnectionError as errc:
        print("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        print("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        print("Error:", err)


cli.add_command(ls)
cli.add_command(inspect)
cli.add_command(add)
cli.add_command(rm)
cli.add_command(modify)
cli.add_command(deployment_time)
cli.add_command(errors)


def console_formatter(uuid, slice_name, created_at, status):
    return "{0: <40}{1: <25}{2: <25}{3: <20}".format(uuid, slice_name, created_at, status)
