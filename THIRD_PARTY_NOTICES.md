# Third-party software and data

The root MIT license covers this project's original code and synthetic fixtures.
It does not replace the licenses of dependencies, public datasets, or referenced
research. No third-party library source has been modified or relicensed here.

## Runtime dependencies

The measured local runtime inventory is in [docs/dependency-licenses.json](docs/dependency-licenses.json).
It records Python distribution metadata and Java POM/parent-POM declarations, with
separate checks for bundled artifacts whose POM does not declare a license. It is
an inventory, not a claim that every component is MIT or a vulnerability audit.

- Python runtime: LangChain/LangGraph/DuckDB and other MIT packages; HTTPX and
  related BSD packages; Apache-2.0 packages; certifi MPL-2.0; and packages with
  compound license expressions. Keep each installed distribution's license files.
- Java runtime: Spring, Tomcat, Jackson, Flyway and associated Apache-2.0 packages;
  PostgreSQL JDBC BSD-2-Clause; SLF4J MIT; Logback EPL-2.0/LGPL-2.1 declarations;
  Jakarta annotations EPL-2.0/GPL-with-Classpath-exception declarations. The
  original license and NOTICE files remain inside the dependency JARs.
- H2 2.4.240 is redistributed as an **unmodified** binary, available under
  MPL-2.0 or EPL-1.0. Its original license is included in
  [third_party/h2-2.4.240-LICENSE.txt](third_party/h2-2.4.240-LICENSE.txt), from
  [the upstream version tag](https://github.com/h2database/h2database/blob/version-2.4.240/LICENSE.txt).
  H2 and this project are supplied without warranty; the original license's
  warranty and liability terms apply. [Upstream license notice](https://h2database.com/html/license.html).
- Python, OpenJDK/Temurin, PostgreSQL, Caddy, Debian and their image packages
  retain their own licenses and notices in their distributions/images. Copying
  the JDK includes its `legal` directory; this project does not remove it.
  A container image digest and package inventory must accompany a verified release.

The optional UCI XLSX prototype uses openpyxl 3.1.5 (MIT), et-xmlfile 2.0.0 (MIT),
and defusedxml 0.7.1 (PSF). They are separate from the Agent runtime lock and are
not required for the CSV application container.

## Public data and examples

The CSV/JSON business fixtures under `examples/` are synthetic. Their Chinese
field names do not indicate data obtained from a Chinese ecommerce platform.
No customer or corporate data is included in this repository.

UCI Online Retail is a separate, optional public data experiment:
Chen, D. (2015), *Online Retail*, UCI Machine Learning Repository,
[DOI 10.24432/C5BW33](https://doi.org/10.24432/C5BW33),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Published aggregate probes apply the mapping/quality rules described in
[docs/data-sources.md](docs/data-sources.md); those are modified analytical results,
not the original dataset. No endorsement by the dataset author is implied.
Raw downloaded files are excluded from the repository and container context.

Olist and other candidate datasets were researched but are not represented as
downloaded or validated project inputs. External model access is governed by
the provider's separate terms; this code license does not grant API access.
