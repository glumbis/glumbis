"""Regenerate dark_mode.svg and light_mode.svg for the GitHub profile README.

Reads ACCESS_TOKEN and USER_NAME from the environment, collects stats from the
GitHub GraphQL API and writes both SVGs from scratch. Per-repo commit / LOC
results are cached in cache/<hash>.txt keyed on each repo's default-branch
commit count, so unchanged repos are not re-walked on every run.
"""

import datetime
import hashlib
import os
import sys
import time
from xml.sax.saxutils import escape

import requests
from dateutil.relativedelta import relativedelta

try:
    from zoneinfo import ZoneInfo
    TODAY = datetime.datetime.now(ZoneInfo("Europe/Oslo")).date()
except Exception:
    TODAY = datetime.date.today()

BIRTHDAY = datetime.date(2008, 1, 27)
WIDTH = 58  # characters per line
API = "https://api.github.com/graphql"
REST_API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
CACHE_VERSION = "# loc-v2: source files only, merges skipped"

THEMES = {
    "dark_mode.svg": dict(bg="#161b22", text="#c9d1d9", key="#ffa657", value="#a5d6ff",
                          cc="#616e7f", add="#3fb950", dele="#f85149"),
    "light_mode.svg": dict(bg="#f6f8fa", text="#24292f", key="#953800", value="#0a3069",
                           cc="#c2cfde", add="#1a7f37", dele="#cf222e"),
}

STATIC_INFO = [
    ("OS", "Windows 11"),
    ("Uptime", None),  # filled in from BIRTHDAY
    ("Host", "Nesodden vgs, University of Oslo"),
    ("Location", "Norway"),
    ("IDE", "VS Code, Visual Studio"),
    ("Engine", "Unity"),
    ("AI", "Claude Code"),
    None,
    ("Languages.Programming", "Python, C#, Lua"),
    ("Languages.Computer", "HTML, CSS"),
    ("Languages.Real", "Norwegian, English, German"),
    None,
    ("Interests.Software", "Game Dev, Video Editing"),
    ("Interests.Hardware", "3D Printing, CAD"),
    ("Interests.Science", "Math, Physics"),
    None,
]


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- GitHub API

class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"bearer {token}"
        self.calls = 0

    def _request(self, method, url, retries=3, **kwargs):
        """Send a request; raises GitHubError rather than returning partial data."""
        for attempt in range(retries + 1):
            try:
                r = self.session.request(method, url, timeout=60, **kwargs)
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(2 ** attempt * 5)
                    continue
                raise GitHubError(f"network error talking to GitHub: {e}")
            self.calls += 1
            if r.status_code in (502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 5)
                continue
            if r.status_code == 401:
                raise GitHubError("GitHub rejected ACCESS_TOKEN (401 Unauthorized). Is the token valid and unexpired?")
            if r.status_code in (403, 429):
                reset = r.headers.get("X-RateLimit-Reset")
                when = (datetime.datetime.fromtimestamp(int(reset)).isoformat() if reset else "unknown")
                raise GitHubError(f"GitHub rate limit or abuse protection hit ({r.status_code}); resets at {when}. "
                                  f"Response: {r.text[:300]}")
            if r.status_code != 200:
                raise GitHubError(f"GitHub API returned {r.status_code} for {url}: {r.text[:300]}")
            return r.json()
        raise GitHubError("GitHub API kept failing after retries")

    def query(self, query, variables):
        data = self._request("POST", API, json={"query": query, "variables": variables})
        if data.get("errors"):
            raise GitHubError(f"GraphQL error: {data['errors']}")
        return data["data"]

    def rest(self, path, **params):
        return self._request("GET", REST_API + path, params=params)


USER_QUERY = """
query($login: String!) {
  user(login: $login) { id }
}"""

REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor,
                 ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        name
        owner { login }
        isFork
        isPrivate
        stargazerCount
        pushedAt
        primaryLanguage { name }
        defaultBranchRef { target { ... on Commit { history { totalCount } } } }
      }
    }
  }
}"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $author: ID!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor, author: {id: $author}) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes { oid parents { totalCount } }
          }
        }
      }
    }
  }
}"""


def get_repos(gh, login):
    repos, cursor = [], None
    while True:
        conn = gh.query(REPOS_QUERY, {"login": login, "cursor": cursor})["user"]["repositories"]
        repos.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            return repos
        cursor = conn["pageInfo"]["endCursor"]


# Lines of code count only hand-written source files. Generated or third-party
# content (Unity .meta/.prefab/.asset files, bundled data like OpenCV's
# haarcascade XML, lock files, minified bundles, vendored folders) is skipped,
# and so are merge commits, which re-count branch work or pull in upstream code.
SOURCE_EXTENSIONS = {
    "py", "pyw", "cs", "lua", "js", "jsx", "mjs", "cjs", "ts", "tsx", "html", "htm", "css", "scss",
    "sass", "less", "vue", "svelte", "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "rs", "go", "java",
    "kt", "kts", "swift", "rb", "php", "sh", "bash", "ps1", "bat", "cmd", "sql", "shader", "hlsl",
    "glsl", "cginc", "compute", "wgsl", "gd", "r", "m", "dart", "scala", "zig", "toml", "yml", "yaml",
}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "packages-lock.json", "cargo.lock",
              "poetry.lock", "uv.lock", "pipfile.lock", "composer.lock", "gemfile.lock"}
SKIP_DIRS = {"node_modules", "vendor", "third_party", "thirdparty", "third-party", "external", "dist",
             "build", "library", "packages", "plugins", "site-packages", ".venv", "venv"}


def is_source(path):
    parts = path.lower().split("/")
    name = parts[-1]
    if name in SKIP_NAMES or any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    if ".min." in name or "." not in name:
        return False
    return name.rsplit(".", 1)[1] in SOURCE_EXTENSIONS


def commit_loc(gh, owner, name, oid):
    """Additions/deletions in source files for one commit (REST gives per-file stats)."""
    additions = deletions = 0
    page = 1
    while True:
        files = gh.rest(f"/repos/{owner}/{name}/commits/{oid}", per_page=300, page=page).get("files", [])
        for f in files:
            if is_source(f["filename"]):
                additions += f["additions"]
                deletions += f["deletions"]
        if len(files) < 300:
            return additions, deletions
        page += 1


def walk_history(gh, repo, author_id):
    """Return (commits, additions, deletions) authored by author_id on the default branch."""
    owner, name = repo["owner"]["login"], repo["name"]
    commits = additions = deletions = 0
    cursor = None
    while True:
        data = gh.query(HISTORY_QUERY, {"owner": owner, "name": name, "cursor": cursor, "author": author_id})
        ref = data["repository"]["defaultBranchRef"]
        if ref is None:
            return 0, 0, 0
        hist = ref["target"]["history"]
        commits = hist["totalCount"]
        for node in hist["nodes"]:
            if node["parents"]["totalCount"] > 1:
                continue
            a, d = commit_loc(gh, owner, name, node["oid"])
            additions += a
            deletions += d
        if not hist["pageInfo"]["hasNextPage"]:
            return commits, additions, deletions
        cursor = hist["pageInfo"]["endCursor"]


def branch_commit_count(repo):
    ref = repo["defaultBranchRef"]
    return ref["target"]["history"]["totalCount"] if ref else 0


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def collect_loc(gh, login, repos, author_id):
    """Sum commits / additions / deletions over all repos, using and refreshing the cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, sha(login) + ".txt")
    cache = {}
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
        # Entries from an older counting method are discarded and recomputed.
        for line in lines[1:] if lines and lines[0] == CACHE_VERSION else []:
                parts = line.split()
                if len(parts) == 5:
                    cache[parts[0]] = tuple(int(p) for p in parts[1:])

    fresh = {}
    for repo in repos:
        key = sha(repo["nameWithOwner"])  # hashed so private repo names never land in the cache
        total = branch_commit_count(repo)
        cached = cache.get(key)
        if cached and cached[0] == total:
            fresh[key] = cached
        else:
            print(f"  walking {repo['nameWithOwner']} ({total} commits on default branch)")
            fresh[key] = (total, *walk_history(gh, repo, author_id))

    with open(path, "w", newline="\n") as f:
        f.write(CACHE_VERSION + "\n")
        for key in sorted(fresh):
            f.write(key + " " + " ".join(str(v) for v in fresh[key]) + "\n")

    commits = sum(v[1] for v in fresh.values())
    added = sum(v[2] for v in fresh.values())
    deleted = sum(v[3] for v in fresh.values())
    return commits, added, deleted


# ------------------------------------------------------------- line builders
# A line is a list of (text, css_class_or_None) segments totalling WIDTH chars.

def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def uptime():
    d = relativedelta(TODAY, BIRTHDAY)
    return f"{plural(d.years, 'year')}, {plural(d.months, 'month')}, {plural(d.days, 'day')}"


def header(title):
    prefix = title + " "
    return [(prefix + "—" * (WIDTH - len(prefix) - 3) + "-—-", None)]


def blank():
    return [(". ", "cc")]


def kv(key, value_segments, min_dots=1):
    """'. Key: ....... value' with the dot leader filling the line to WIDTH."""
    value_len = sum(len(t) for t, _ in value_segments)
    dots = WIDTH - (2 + len(key) + 2 + 1 + value_len)
    if dots < min_dots:
        return None
    return [(". ", "cc"), (key, "key"), (": ", None), ("." * dots + " ", "cc")] + value_segments


def kv_required(key, value):
    line = kv(key, [(value, "value")])
    if line is None:
        fail(f"'{key}: {value}' does not fit in {WIDTH} characters")
    return line


def project_line(name, language):
    line = kv(name, [(language, "value")], min_dots=3)
    if line is None:
        room = WIDTH - (2 + 2 + 3 + 1 + len(language))  # max key length with 3 dots
        name = name[:room - 3] + "..."
        line = kv(name, [(language, "value")], min_dots=3)
    return line


def repos_line(owned, contributed, stars):
    owned, contributed, stars = f"{owned:,}", f"{contributed:,}", f"{stars:,}"
    fixed = (len(". Repos: ") + 1 + len(owned) + len(" {Contributed: ") + len(contributed)
             + len("} | Stars: ") + 1 + len(stars))
    spare = WIDTH - fixed
    # Keep the Stars column roughly right-aligned like the original: give it a short leader.
    right = max(1, min(spare - 1, 8 - len(stars)))
    left = spare - right
    if left < 1:
        fail("Repos/Stars line does not fit in 58 characters")
    return [(". ", "cc"), ("Repos", "key"), (": ", None), ("." * left + " ", "cc"), (owned, "value"),
            (" {", None), ("Contributed", "key"), (": ", None), (contributed, "value"), ("} | ", None),
            ("Stars", "key"), (": ", None), ("." * right + " ", "cc"), (stars, "value")]


def loc_line(added, deleted):
    value = [(f"{added - deleted:,}", "value"), (" ( ", None), (f"{added:,}++", "add"),
             (", ", None), (f"{deleted:,}--", "dele"), (" )", None)]
    for key in ("Lines of Code on GitHub", "Lines of Code", "LOC"):
        line = kv(key, value)
        if line:
            return line
    fail("Lines of Code line does not fit in 58 characters")


# ------------------------------------------------------------------ SVG

def render(lines, c):
    height = 34 + (len(lines) - 1) * 20 + 24
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace" '
        f'width="624px" height="{height}px" font-size="16px">',
        "<style>",
        '@font-face { src: local("Consolas"), local("Consolas Bold"); font-family: "ConsolasFallback"; '
        "font-display: swap; -webkit-size-adjust: 109%; size-adjust: 109%; }",
        f".key {{fill: {c['key']};}}",
        f".value {{fill: {c['value']};}}",
        f".cc {{fill: {c['cc']};}}",
        f".add {{fill: {c['add']};}}",
        f".dele {{fill: {c['dele']};}}",
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="624px" height="{height}px" fill="{c["bg"]}" rx="15"/>',
        f'<text x="22" y="34" fill="{c["text"]}">',
    ]
    for i, segments in enumerate(lines):
        length = sum(len(t) for t, _ in segments)
        if length != WIDTH and segments != blank():
            fail(f"internal: line {i} is {length} chars, expected {WIDTH}: {segments}")
        body = "".join(escape(t) if cls is None else f'<tspan class="{cls}">{escape(t)}</tspan>'
                       for t, cls in segments)
        out.append(f'<tspan x="22" y="{34 + i * 20}">{body}</tspan>')
    out += ["</text>", "</svg>", ""]
    return "\n".join(out)


def main():
    token, login = os.environ.get("ACCESS_TOKEN"), os.environ.get("USER_NAME")
    if not token or not login:
        fail("set ACCESS_TOKEN and USER_NAME environment variables")

    gh = GitHub(token)
    user = gh.query(USER_QUERY, {"login": login})["user"]
    if user is None:
        fail(f"GitHub user '{login}' not found")

    print("fetching repositories...")
    repos = get_repos(gh, login)
    owned = [r for r in repos if r["owner"]["login"].lower() == login.lower()]
    stars = sum(r["stargazerCount"] for r in owned)

    projects = sorted(
        (r for r in owned if not r["isFork"] and not r["isPrivate"]
         and r["nameWithOwner"].lower() != f"{login}/{login}".lower() and r["pushedAt"]),
        key=lambda r: r["pushedAt"], reverse=True)[:3]

    print("counting commits and lines of code...")
    commits, added, deleted = collect_loc(gh, login, repos, user["id"])

    lines = [header("lukas@dam")]
    for item in STATIC_INFO:
        if item is None:
            lines.append(blank())
        else:
            key, value = item
            lines.append(kv_required(key, uptime() if key == "Uptime" else value))
    lines.append(header("- Projects"))
    for r in projects:
        lines.append(project_line(r["name"], (r["primaryLanguage"] or {}).get("name", "—")))
    lines.append(blank())
    lines.append(header("- GitHub Stats"))
    lines.append(repos_line(len(owned), len(repos), stars))
    lines.append(kv_required("Commits", f"{commits:,}"))
    lines.append(loc_line(added, deleted))

    for filename, colours in THEMES.items():
        with open(os.path.join(HERE, filename), "w", encoding="utf-8", newline="\n") as f:
            f.write(render(lines, colours))
    print(f"done in {gh.calls} API calls: {len(owned)} owned / {len(repos)} total repos, {stars} stars, "
          f"{commits} commits, +{added} -{deleted}")


if __name__ == "__main__":
    try:
        main()
    except GitHubError as e:
        fail(f"{e} (SVGs were not modified)")
