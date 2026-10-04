"""Publish a verified release archive using the repository's Git credentials."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", default="koolakoff/analog-fpv-compressor")
    parser.add_argument("--stable", action="store_true")
    args = parser.parse_args()
    archive = args.archive.resolve()
    checksum = archive.with_suffix(".zip.sha256")
    expected = checksum.read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("Archive checksum does not match")
    with zipfile.ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read(next(name for name in zipped.namelist() if name.endswith("/BUILD.json"))))
    if manifest["source_has_local_changes"]:
        raise RuntimeError("Only an archive built from a committed, clean source tree can be published")
    if args.tag != "v" + manifest["application_version"]:
        raise RuntimeError("Tag does not match the archived application version")
    token_record = subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", "credential", "fill"],
                                  input="protocol=https\nhost=github.com\n\n", text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    credentials = dict(line.split("=", 1) for line in token_record.stdout.splitlines() if "=" in line)
    token = credentials.get("password")
    if not token:
        raise RuntimeError("Git credential manager did not return GitHub credentials")

    def request(url, method="GET", data=None, content_type="application/json"):
        raw = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
        headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "analog-fpv-compressor-release"}
        if raw is not None:
            headers["Content-Type"] = content_type
        with urllib.request.urlopen(urllib.request.Request(url, data=raw, headers=headers, method=method), timeout=180) as response:
            return json.load(response)

    api = "https://api.github.com/repos/" + args.repo
    tag = request(api + "/git/ref/tags/" + urllib.parse.quote(args.tag, safe=""))
    if tag["object"]["type"] != "commit" or tag["object"]["sha"] != manifest["source_revision"]:
        raise RuntimeError("Remote lightweight tag must point to the exact archived commit")
    release = request(api + "/releases", "POST", {
        "tag_name": args.tag, "name": args.tag + " — Windows x64 CLI",
        "body": args.notes.read_text(encoding="utf-8"), "draft": True,
        "prerelease": not args.stable})
    for asset in (archive, checksum):
        upload = release["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(asset.name)
        result = request(upload, "POST", asset.read_bytes(),
                         "application/zip" if asset == archive else "text/plain")
        if result["size"] != asset.stat().st_size:
            raise RuntimeError("Uploaded asset size does not match")
        if asset == archive and result.get("digest") and result["digest"] != "sha256:" + actual:
            raise RuntimeError("GitHub asset digest does not match")
    published = request(api + f"/releases/{release['id']}", "PATCH", {"draft": False})
    print(json.dumps({"url": published["html_url"], "tag": args.tag,
                      "assets": [{"name": asset["name"], "bytes": asset["size"],
                                  "url": asset["browser_download_url"], "digest": asset.get("digest")}
                                 for asset in published["assets"]]}, indent=2))


if __name__ == "__main__":
    main()
