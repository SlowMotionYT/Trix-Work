import fnmatch
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import zipfile

SITE = os.environ["TRIX_SITE"].rstrip("/")
SECRET = os.environ["TRIX_SECRET"]
JOB = os.environ["TRIX_JOB"]
TARGET = os.environ["TRIX_TARGET"]
CHUNKS = int(os.environ["TRIX_CHUNKS"])
NAME = re.sub(r"[^A-Za-z0-9._-]+", "_", os.environ.get("TRIX_NAME") or "app").strip("._") or "app"
CH = 3 * 1024 * 1024
ROOT = os.getcwd()
WORK = os.path.join(ROOT, "_trix_work")
OUT = os.path.join(ROOT, "_trix_out")
KS = os.path.join(ROOT, "_trix.jks")
KS_PASS = "trixwork"

LOG = []
S = {"stage": "Preparando", "progress": 5, "message": "", "compiling": False, "t0": 0.0}
STOP = threading.Event()
TICK = None


def call(method, path, data=None, ctype="application/json", tries=5):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(
                SITE + path,
                data=data,
                method=method,
                headers={"x-trix-secret": SECRET, "content-type": ctype, "user-agent": "trix-runner"},
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 * (n + 1))
    raise last


def report(**kw):
    kw["job"] = JOB
    try:
        call("POST", "/.netlify/functions/report", json.dumps(kw).encode())
    except Exception as e:
        print("report failed:", e, flush=True)


def set_stage(stage, progress):
    S["stage"] = stage
    S["progress"] = progress
    S["message"] = ""
    report(status="running", stage=stage, progress=progress, message="")


def ticker():
    while not STOP.wait(4):
        if S["compiling"]:
            S["progress"] = int(35 + 50 * (1 - math.exp(-(time.time() - S["t0"]) / 240)))
        report(status="running", stage=S["stage"], progress=S["progress"], message=S["message"])


def child_env(extra=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("TRIX_") and k != "GITHUB_TOKEN"}
    if extra:
        e.update(extra)
    return e


def log(line):
    LOG.append(line)
    print(line, flush=True)


def run(cmd, cwd=None, env=None):
    log("$ " + cmd)
    p = subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        env=child_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in p.stdout:
        line = line.rstrip("\r\n")
        log(line)
        if line.strip():
            S["message"] = line.strip()[:140]
    return p.wait()


def capture(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env())
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return str(e)


PAT = re.compile(r"(error|failed|failure|exception|fatal|what went wrong|cannot|could not|unresolved|not found|no se )", re.I)


def build_errors():
    hits, seen = [], set()
    i = 0
    while i < len(LOG) and len(hits) < 150:
        l = LOG[i]
        if l.strip() and PAT.search(l) and l not in seen:
            seen.add(l)
            hits.append(l)
            if "what went wrong" in l.lower():
                hits.extend(LOG[i + 1 : i + 9])
        i += 1
    text = "\n".join(hits)
    if text:
        text += "\n\n"
    text += "----- final del log -----\n" + "\n".join(LOG[-40:])
    return text[-60000:]


def stop_ticker():
    STOP.set()
    if TICK is not None:
        TICK.join()


def fail(msg=None):
    if msg:
        log(msg)
    S["compiling"] = False
    stop_ticker()
    report(status="failed", stage="Error", progress=S["progress"], errors=build_errors(), message="", purgeSrc=True)
    sys.exit(1)


def find_all(root, patterns, depth=3):
    found = []
    base = root.rstrip(os.sep).count(os.sep)
    for dp, dn, fn in os.walk(root):
        d = dp.count(os.sep) - base
        if d > depth:
            dn[:] = []
            continue
        dn[:] = [x for x in dn if x not in ("node_modules", ".git", "build", "target", "__MACOSX")]
        for pat in patterns:
            for n in fnmatch.filter(fn, pat):
                found.append((d, os.path.join(dp, n)))
    found.sort(key=lambda x: (x[0], x[1]))
    return [p for _, p in found]


def find_file(root, patterns, depth=3):
    r = find_all(root, patterns, depth)
    return r[0] if r else None


def find_root(d):
    while True:
        items = [x for x in os.listdir(d) if x != "__MACOSX" and x != ".DS_Store"]
        if len(items) == 1 and os.path.isdir(os.path.join(d, items[0])):
            d = os.path.join(d, items[0])
            continue
        return d


def q(p):
    return '"' + p + '"'


def gradle_project(root):
    p = find_file(root, ["gradlew"]) or find_file(root, ["settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"])
    return os.path.dirname(p) if p else None


def gradle_cmd(proj):
    wrapper = os.path.join(proj, "gradlew")
    jar = os.path.join(proj, "gradle", "wrapper", "gradle-wrapper.jar")
    if os.path.exists(wrapper) and os.path.exists(jar):
        os.chmod(wrapper, 0o755)
        return "./gradlew"
    return "gradle"


def biggest(files):
    return max(files, key=os.path.getsize) if files else None


def vkey(p):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", os.path.basename(p))]


def ensure_keystore():
    if not os.path.exists(KS):
        rc = run(
            f"keytool -genkeypair -keystore {q(KS)} -storepass {KS_PASS} -keypass {KS_PASS} -alias trix "
            f"-keyalg RSA -keysize 2048 -validity 10000 -dname \"CN=Trix Work\""
        )
        if rc:
            fail("No se pudo crear la clave de firma")


def build_android(root):
    proj = gradle_project(root)
    if not proj:
        fail("No se encontro un proyecto Gradle")
    task = {"apk-debug": "assembleDebug", "apk-release": "assembleRelease", "aab": "bundleRelease"}[TARGET]
    rc = run(f"{gradle_cmd(proj)} {task} --no-daemon --console=plain", cwd=proj)
    if rc:
        fail(f"Gradle termino con codigo {rc}")

    ext = "aab" if TARGET == "aab" else "apk"
    sub = "bundle" if ext == "aab" else "apk"
    files = glob.glob(os.path.join(proj, "**", "build", "outputs", sub, "**", "*." + ext), recursive=True)
    files = [f for f in files if "androidTest" not in f]
    if TARGET == "apk-debug":
        files = [f for f in files if "debug" in os.path.basename(f).lower()] or files
    if TARGET == "apk-release":
        files = [f for f in files if "release" in os.path.basename(f).lower()] or files
    src = biggest(files)
    if not src:
        fail("No se encontro el archivo generado")

    if TARGET == "apk-debug":
        return src, f"{NAME}-debug.apk"

    if TARGET == "apk-release":
        final = os.path.join(OUT, f"{NAME}-release.apk")
        if "unsigned" in os.path.basename(src).lower():
            S["stage"] = "Firmando"
            ensure_keystore()
            sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
            tools = sorted(glob.glob(os.path.join(sdk, "build-tools", "*")), key=vkey)
            if not tools:
                fail("No se encontraron build-tools de Android")
            bt = tools[-1]
            aligned = os.path.join(ROOT, "_aligned.apk")
            if os.path.exists(aligned):
                os.remove(aligned)
            if run(f"{q(os.path.join(bt, 'zipalign'))} -p -f 4 {q(src)} {q(aligned)}"):
                fail("Fallo zipalign")
            if run(
                f"{q(os.path.join(bt, 'apksigner'))} sign --ks {q(KS)} --ks-pass pass:{KS_PASS} "
                f"--key-pass pass:{KS_PASS} --out {q(final)} {q(aligned)}"
            ):
                fail("Fallo apksigner")
            return final, os.path.basename(final)
        return src, os.path.basename(final)

    final = os.path.join(OUT, f"{NAME}-release.aab")
    shutil.copyfile(src, final)
    if "jar is unsigned" in capture(f"jarsigner -verify {q(final)}").lower():
        S["stage"] = "Firmando"
        ensure_keystore()
        if run(
            f"jarsigner -keystore {q(KS)} -storepass {KS_PASS} -keypass {KS_PASS} "
            f"-sigalg SHA256withRSA -digestalg SHA-256 {q(final)} trix"
        ):
            fail("Fallo jarsigner")
    return final, os.path.basename(final)


def build_jar(root):
    g = find_file(root, ["build.gradle", "build.gradle.kts"])
    m = find_file(root, ["pom.xml"])
    if not g and not m:
        fail("No se encontro build.gradle ni pom.xml")
    use_gradle = bool(g) and (not m or g.count(os.sep) <= m.count(os.sep))
    if use_gradle:
        proj = os.path.dirname(g)
        try:
            text = open(g, encoding="utf-8", errors="replace").read().lower()
        except Exception:
            text = ""
        task = "shadowJar" if "shadow" in text else "build"
        rc = run(f"{gradle_cmd(proj)} {task} --no-daemon --console=plain", cwd=proj)
        pattern = os.path.join(proj, "**", "build", "libs", "*.jar")
    else:
        proj = os.path.dirname(m)
        mvnw = os.path.join(proj, "mvnw")
        if os.path.exists(mvnw):
            os.chmod(mvnw, 0o755)
            tool = "./mvnw"
        else:
            tool = "mvn"
        rc = run(f"{tool} -B -DskipTests package", cwd=proj)
        pattern = os.path.join(proj, "**", "target", "*.jar")
    if rc:
        fail(f"La compilacion termino con codigo {rc}")
    jars = [
        j
        for j in glob.glob(pattern, recursive=True)
        if not re.search(r"(sources|javadoc|original|plain)", os.path.basename(j), re.I)
    ]
    src = biggest(jars)
    if not src:
        fail("No se encontro el archivo JAR generado")
    return src, f"{NAME}.jar"


def build_exe(root):
    outdir = os.path.join(ROOT, "_exe_out")
    os.makedirs(outdir, exist_ok=True)
    exe_name = f"{NAME}.exe"
    target_path = os.path.join(outdir, exe_name)

    csprojs = find_all(root, ["*.csproj"])
    if csprojs:
        pick = csprojs[0]
        for c in csprojs:
            try:
                if "exe" in open(c, encoding="utf-8", errors="replace").read().lower().split("<outputtype>")[1][:12]:
                    pick = c
                    break
            except Exception:
                pass
        pubdir = os.path.join(outdir, "dotnet")
        rc = run(
            f"dotnet publish {q(pick)} -c Release -r win-x64 --self-contained true "
            f"-p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o {q(pubdir)}"
        )
        if rc:
            fail(f"dotnet termino con codigo {rc}")
        exes = [e for e in glob.glob(os.path.join(pubdir, "*.exe")) if "createdump" not in e.lower()]
        src = biggest(exes)
        if not src:
            fail("No se encontro el EXE generado")
        return src, exe_name

    pj = find_file(root, ["package.json"], 1)
    if pj:
        proj = os.path.dirname(pj)
        try:
            pkg = json.load(open(pj, encoding="utf-8"))
        except Exception:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if run("npm install", cwd=proj):
            fail("npm install fallo")
        if "electron" in deps:
            if run("npx --yes electron-builder --win portable --publish never", cwd=proj):
                fail("electron-builder fallo")
            src = biggest(glob.glob(os.path.join(proj, "dist", "*.exe")))
        else:
            if run(f"npx --yes @yao-pkg/pkg . --targets node20-win-x64 --output {q(target_path)}", cwd=proj):
                fail("pkg fallo")
            src = target_path if os.path.exists(target_path) else None
        if not src:
            fail("No se encontro el EXE generado")
        return src, exe_name

    entry = None
    for n in ("main.py", "app.py", "__main__.py", "run.py"):
        entry = find_file(root, [n], 1)
        if entry:
            break
    if not entry:
        pys = find_all(root, ["*.py"], 0)
        if len(pys) == 1:
            entry = pys[0]
    if entry:
        proj = os.path.dirname(entry)
        py = q(sys.executable)
        req = os.path.join(proj, "requirements.txt")
        if os.path.exists(req) and run(f"{py} -m pip install -r requirements.txt", cwd=proj):
            fail("pip install fallo")
        if run(f"{py} -m pip install pyinstaller"):
            fail("No se pudo instalar pyinstaller")
        if run(f"{py} -m PyInstaller --onefile --noconfirm --name {NAME} {q(entry)}", cwd=proj):
            fail("PyInstaller fallo")
        src = os.path.join(proj, "dist", exe_name)
        if not os.path.exists(src):
            fail("No se encontro el EXE generado")
        return src, exe_name

    gomod = find_file(root, ["go.mod"], 1)
    if gomod:
        proj = os.path.dirname(gomod)
        if run(f'go build -ldflags "-s -w" -o {q(target_path)} .', cwd=proj):
            fail("go build fallo")
        return target_path, exe_name

    cargo = find_file(root, ["Cargo.toml"], 1)
    if cargo:
        proj = os.path.dirname(cargo)
        if run("cargo build --release", cwd=proj):
            fail("cargo build fallo")
        src = biggest(glob.glob(os.path.join(proj, "target", "release", "*.exe")))
        if not src:
            fail("No se encontro el EXE generado")
        return src, exe_name

    cm = find_file(root, ["CMakeLists.txt"], 1)
    if cm:
        proj = os.path.dirname(cm)
        if run("cmake -S . -B _build -DCMAKE_BUILD_TYPE=Release", cwd=proj):
            fail("cmake configure fallo")
        if run("cmake --build _build --config Release", cwd=proj):
            fail("cmake build fallo")
        exes = [e for e in glob.glob(os.path.join(proj, "_build", "**", "*.exe"), recursive=True) if "CMakeFiles" not in e]
        src = biggest(exes)
        if not src:
            fail("No se encontro el EXE generado")
        return src, exe_name

    fail("Tipo de proyecto no reconocido para EXE (se admite .NET, Node, Python, Go, Rust y CMake)")


def main():
    global TICK
    TICK = threading.Thread(target=ticker, daemon=True)
    TICK.start()

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    set_stage("Descargando proyecto", 8)
    zp = os.path.join(ROOT, "src.zip")
    with open(zp, "wb") as f:
        for i in range(CHUNKS):
            f.write(call("GET", f"/.netlify/functions/chunk?kind=src&job={JOB}&i={i}"))
            S["progress"] = 8 + int(12 * (i + 1) / CHUNKS)

    set_stage("Extrayendo", 22)
    try:
        with zipfile.ZipFile(zp) as z:
            z.extractall(WORK)
    except Exception as e:
        fail(f"ZIP invalido: {e}")
    root = find_root(WORK)

    S["stage"] = "Compilando"
    S["progress"] = 35
    S["t0"] = time.time()
    S["compiling"] = True
    report(status="running", stage="Compilando", progress=35, message="")

    if TARGET in ("apk-debug", "apk-release", "aab"):
        path, out_name = build_android(root)
    elif TARGET == "jar":
        path, out_name = build_jar(root)
    elif TARGET == "exe":
        path, out_name = build_exe(root)
    else:
        fail("Destino desconocido")

    S["compiling"] = False
    S["stage"] = "Subiendo resultado"
    S["progress"] = 90
    report(status="running", stage="Subiendo resultado", progress=90, message="")

    dst = os.path.join(OUT, out_name)
    if os.path.abspath(path) != os.path.abspath(dst):
        shutil.copyfile(path, dst)
    size = os.path.getsize(dst)
    n = 0
    with open(dst, "rb") as f:
        while True:
            b = f.read(CH)
            if not b:
                break
            call("POST", f"/.netlify/functions/chunk?kind=out&job={JOB}&i={n}", b, "application/octet-stream")
            n += 1
            S["progress"] = min(99, 90 + int(9 * f.tell() / max(size, 1)))

    stop_ticker()
    report(status="done", stage="Listo", progress=100, outName=out_name, outChunks=n, outSize=size, message="", purgeSrc=True)
    print("Listo:", out_name, size, flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log(traceback.format_exc())
        fail()
