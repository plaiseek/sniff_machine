import docker
import io
import os
import sys
import threading


def build_image(image_tag: str, dockerfile: str) -> None:
    print(f"Building {image_tag} Docker image...")
    client = docker.from_env()
    try:
        logs = client.api.build(
            fileobj=io.BytesIO(dockerfile.encode("utf-8")),
            tag=image_tag,
            rm=True,
            forcerm=True,
            decode=True,
        )
        # The low-level API never raises BuildError: a failed step arrives as an {"error": ...} entry
        for entry in logs:
            line = entry.get("stream", "").rstrip("\n")
            if len(line) > 0:
                print(line)
            if "error" in entry:
                message = entry.get("errorDetail", {}).get("message") or entry["error"]
                print(f"Failed to build {image_tag}: {message}")
                sys.exit(os.EX_CONFIG)
    except docker.errors.APIError as e:
        print(f"Failed to build {image_tag}: {e}")
        sys.exit(os.EX_CONFIG)


def run_container(image_tag: str, params: dict):
    print(f"Running {image_tag} Docker container ({params})")
    client = docker.from_env()
    return client.containers.run(image_tag, remove=False, **params)


def _stream_container_logs(container, log_prefix):
    try:
        for raw_line in container.logs(stream=True, follow=True):
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                print(f"{log_prefix} {line}")
    except docker.errors.NotFound:
        pass


def print_containers_logs(container_prefix_pairs):
    threads = []
    for container, log_prefix in container_prefix_pairs:
        t = threading.Thread(
            target=_stream_container_logs,
            args=(container, log_prefix),
            daemon=True,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


def stop_containers(containers) -> None:
    for container in containers:
        container.remove(force=True)


def stop_containers_by_tag(tag: str) -> None:
    client = docker.from_env()
    stop_containers(client.containers.list(all=True, filters={"ancestor": tag}))
