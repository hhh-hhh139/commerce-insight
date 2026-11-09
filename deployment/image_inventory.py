"""Read installed packages from this CI run's images without networks or secrets."""
import json


APPLICATION_METADATA = r'''
import hashlib, importlib.metadata, json, platform, re, zipfile
from pathlib import Path
root=Path('/app')
normalize=lambda name: re.sub(r'[-_.]+','-',name).lower()
packages=sorted([{'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()],key=lambda p:normalize(p['name']))
installed={normalize(p['name']):p['version'] for p in packages}
locked=dict((normalize(name),version) for name,version in re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)',(root/'requirements-agent.lock').read_text(),re.M))
assert locked and all(installed.get(name)==version for name,version in locked.items())
jar=root/'services/business-api/target/business-api-0.1.0.jar'
with jar.open('rb') as stream: jar_sha=hashlib.file_digest(stream,'sha256').hexdigest()
with zipfile.ZipFile(jar) as archive:
    libraries=[{'file':Path(name).name,'sha256':hashlib.sha256(archive.read(name)).hexdigest()}
               for name in sorted(archive.namelist()) if name.startswith('BOOT-INF/lib/') and name.endswith('.jar')]
assert libraries and any(p['file'].startswith('spring-boot-') for p in libraries)
java={line.split('=',1)[0]:line.split('=',1)[1].strip('"') for line in (root/'.local/java-toolchain/jdk-runtime/release').read_text().splitlines() if '=' in line}
print(json.dumps({'python_version':platform.python_version(),'python_distributions':packages,'locked_distribution_count':len(locked),'lock_matches_installed':True,
 'java_release':{k:java[k] for k in ('JAVA_VERSION','IMPLEMENTOR','OS_NAME','OS_ARCH')},'application_jar_sha256':jar_sha,'java_libraries':libraries}))
'''


def collect_inventory(run, docker, images):
    references = {"application": "commerce-insight:local", "maintenance": "commerce-insight:maintenance",
                  "postgres": images["postgres"]["resolved"], "proxy": images["caddy"]["resolved"]}
    result = {"kind": "installed_image_software_inventory", "real_model_calls": 0,
              "scope": "OS package databases, Python distributions, application jar libraries and JDK release metadata",
              "not_a_vulnerability_or_complete_binary_dependency_scan": True, "images": {}}

    def read(reference, entrypoint, *args):
        return run([*docker, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--memory", "256m", "--pids-limit", "32",
                    "--entrypoint", entrypoint, reference, *args], capture=True)

    for name, reference in references.items():
        metadata = json.loads(run([*docker, "image", "inspect", reference, "--format",
                                   '{{json .}}'], capture=True))
        item = {"reference": reference, "image_id": metadata["Id"], "os": metadata["Os"],
                "architecture": metadata["Architecture"]}
        if name == "proxy":
            item["package_manager"] = "apk"
            item["os_packages"] = sorted(read(reference, "apk", "info", "-v").splitlines())
            item["caddy_version"] = read(reference, "caddy", "version")
        else:
            rows = read(reference, "dpkg-query", "-W",
                        "-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n").splitlines()
            packages = []
            for row in rows:
                package, version, architecture, status = row.split("\t")
                if status == "installed":
                    packages.append({"name": package, "version": version, "architecture": architecture})
            item["package_manager"] = "dpkg"
            item["os_packages"] = sorted(packages, key=lambda p: (p["name"], p["architecture"]))
        assert item["os_packages"], "empty_image_package_inventory"
        result["images"][name] = item
    result["application"] = json.loads(read(references["application"], "/opt/agent-venv/bin/python", "-c", APPLICATION_METADATA))
    return result
