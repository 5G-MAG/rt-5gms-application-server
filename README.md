<p align="center">
  <img src=".github/banner.svg" width="100%" alt="Reference Tools · 5G Media Streaming (5GMS): 5GMS Application Server (AS)">
</p>

<p align="center">
  The downlink 5GMS Application Server (AS): the network function that ingests media content and
  serves it to 5GMSd Clients, as defined in ETSI TS 126.501.
</p>

<p align="center">
  <img alt="Status: under development"
    src="https://img.shields.io/badge/Status-Under%20Development-e67e22">
  <a href="https://github.com/5G-MAG/rt-5gms-application-server/releases"><img alt="Version"
    src="https://img.shields.io/github/v/release/5G-MAG/rt-5gms-application-server?label=Version"></a>
  <a href="LICENSE"><img alt="License: 5G-MAG Public License v1.0"
    src="https://img.shields.io/badge/License-5G--MAG%20PL%20v1.0-blue"></a>
</p>

<p align="center">
  <a href="https://www.5g-mag.com/reference-tools/5gms/">Project page</a> &nbsp;&middot;&nbsp;
  <a href="https://github.com/5G-MAG/rt-5gms-application-server/issues">Issues</a> &nbsp;&middot;&nbsp;
  <a href="https://www.5g-mag.com/contributing">Contributing</a>
</p>

---

## At a glance

|  |  |
|---|---|
| **Implements** | ETSI TS 126.501 (no version stated in the repository) |
| **Part of** | [5G Media Streaming (5GMS)](https://www.5g-mag.com/reference-tools/5gms/), alongside [cmcd-toolkit](https://github.com/5G-MAG/cmcd-toolkit), [rt-5gc-service-consumers](https://github.com/5G-MAG/rt-5gc-service-consumers), [rt-5gms-application](https://github.com/5G-MAG/rt-5gms-application), [rt-5gms-application-function](https://github.com/5G-MAG/rt-5gms-application-function), [rt-5gms-application-provider](https://github.com/5G-MAG/rt-5gms-application-provider), [rt-5gms-common-android-library](https://github.com/5G-MAG/rt-5gms-common-android-library), [rt-5gms-examples](https://github.com/5G-MAG/rt-5gms-examples), [rt-5gms-media-session-handler](https://github.com/5G-MAG/rt-5gms-media-session-handler), [rt-5gms-media-stream-handler](https://github.com/5G-MAG/rt-5gms-media-stream-handler), [rt-cmmf-encoder](https://github.com/5G-MAG/rt-cmmf-encoder), [rt-media-origin](https://github.com/5G-MAG/rt-media-origin) |

## Introduction

This repository is the downlink 5GMS Application Server (5GMSd AS): a small Python daemon that
implements the AS configuration service at reference point M3, and manages an external web
server/proxy daemon that ingests content at M2d and serves it to 5GMSd Clients at M4d. It is
configured over M3 by an M3 client such as the
[5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function) (release v1.1.0 or later).

More information is on the [project page](https://www.5g-mag.com/reference-tools/5gms/).

### 5GMSd AS

The 5GMSd AS provides downlink 5G Media Streaming services to 5GMSd Clients, and can be deployed in
the Trusted Data Network or in an External Data Network. It is the logical function for the data
plane of the 5GMSd System, proxying media content in a way similar to a Content Delivery Network:

- Content is ingested from 5GMSd Application Providers at reference point M2d. The architecture
  allows push- and pull-based ingest over HTTP; this implementation supports pull ingest only.
- Ingested content, possibly manipulated by the AS, is distributed to 5GMSd Clients at reference
  point M4d, using standard pull-based retrieval protocols such as DASH.

### About the implementation

The web server or reverse proxy is an external daemon. The AS writes its configuration files
dynamically and manages the daemon's process lifecycle. At present the only daemon the AS can
control is [Openresty](https://openresty.org/), which is based on nginx.

## Specification

The repository names ETSI TS 126.501 but no version. At build time the Python bindings are generated
from `TS26512_M1_ContentHostingProvisioning` in the 3GPP 5G APIs, branch `REL-17`, and from the
repository's M3 API definition, `M3_merged` (defaults in `build_scripts/generate_5gms_as_openapi`).

Clause-by-clause coverage, and what is still absent, is recorded on the project page rather than
here: <https://www.5g-mag.com/reference-tools/5gms/>

## Install dependencies

```
sudo apt install git python3-pip python3-venv
sudo python3 -m pip install --upgrade pip build setuptools
```

### Openresty

The Application Server needs a web proxy server, and at present the only one it can use is
Openresty, so install the Openresty package for your distribution. Instructions are on the
[Openresty website](https://openresty.org/en/download.html), for
[Linux distributions](https://openresty.org/en/linux-packages.html) and
[Microsoft Windows](https://openresty.org/en/download.html#windows). The Openresty version of nginx
must also be the first one on the system path:

```bash
PATH="/usr/local/openresty/nginx/sbin:$PATH" export PATH
```

### Prerequisites for building

Building a distribution, or installing from source, additionally needs `wget` and `java`:

```
sudo apt install wget default-jdk
```

## Downloading

Release sdist tar files are on the [releases](https://github.com/5G-MAG/rt-5gms-application-server/releases)
page. To get the source, clone the repository:

```
cd ~
git clone --recurse-submodules https://github.com/5G-MAG/rt-5gms-application-server.git
```

## Building

To build a Python sdist distribution tar:

```
cd ~/rt-5gms-application-server
python3 -m build --sdist
```

The sdist tar file is written to the `dist` subdirectory.

## Installing

### Install from sdist

Install a distribution sdist tar file with pip:

```
sudo python3 -m pip install rt-5gms-application-server-<version>.tar.gz
```

When installing as an unprivileged user, the files go to a local installation directory in your
home directory, and a warning says that this directory should be added to your path, with a
command like `PATH="${PATH}:${HOME}/.local/bin" export PATH`.

### Install direct from source

To install directly from the source, first install the `wget` and `java` build prerequisites (see
[Prerequisites for building](#prerequisites-for-building)), then:

```
cd ~/rt-5gms-application-server
sudo python3 -m pip install .
```

### Installing in a virtual Python environment

To try the project out without disturbing your system packages, install it in a Python virtual
environment. This also needs the `wget` and `java` prerequisites (see
[Prerequisites for building](#prerequisites-for-building)). Then:

```
cd ~/rt-5gms-application-server
python3 -m venv venv
venv/bin/python3 -m pip install .
```

In the instructions that follow, either use `venv/bin/5gms-application-server` in place of
`5gms-application-server`, or activate the virtual environment with `source venv/bin/activate`,
which puts the `venv/bin` directory early in the executable search path, and use the
`5gms-application-server` command as written.

## Running

Once [installed](#installing), the application server is run with this command syntax:

```
Syntax: 5gms-application-server [-c <configuration-file>]
```

Most distributions set the Nginx service to start on boot, and some start it as soon as
nginx/openresty is installed. A running default nginx configuration claims TCP port 80, which the
application server then cannot use. Disable and stop the nginx and openresty services first, for
example:

```bash
systemctl disable --now nginx.service openresty.service
```

If either service is not present, an error is displayed; it is safe to ignore.

Command line help is shown with the `-h` flag:

```
5gms-application-server -h
```

Once running, the AS is managed by an M3 client, such as the
[5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function). For standalone configuration
for testing, see the "Testing without the Application Function" section of the
[development documentation](https://www.5g-mag.com/reference-tools/5gms/tutorials/testing-AS#testing-without-the-application-function).

### Docker setup

A Docker Compose setup with the 5GMSd AF and the 5GMSd AS is in the
[rt-5gms-examples](https://github.com/5G-MAG/rt-5gms-examples/tree/development/5gms-docker-setup) project.

## Configuration

The default configuration requires the application server to run as the root user, because it uses
the privileged port 80 and keeps its logs and caches in root-owned directories. To run it as an
unprivileged user, create and use an alternative configuration file, as described in the
[development documentation](https://www.5g-mag.com/reference-tools/5gms/tutorials/testing-AS#running-the-example-without-building).

## Development

This project follows the
[Gitflow workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow).
The `development` branch is the integration branch for new features, so switch to it before starting
work on a new feature. Further development and testing information is on the
[testing page](https://www.5g-mag.com/reference-tools/5gms/tutorials/testing-AS).

## Contributing

Contributions are welcome. How to raise an issue, fork the repository and open a pull request, and
the Contributor License Agreement required before code can be merged, are described at
<https://www.5g-mag.com/contributing>.

## License

Distributed under the 5G-MAG Public License v1.0. See [LICENSE](LICENSE). Third-party
software the Application Server uses is listed in [ATTRIBUTION_NOTICE](ATTRIBUTION_NOTICE).
