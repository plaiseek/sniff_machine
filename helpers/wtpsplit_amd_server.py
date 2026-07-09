import helpers.docker as dk
import base64


def build_wtpsplit_image(sat_model="sat-3l-sm") -> None:

    server_script = f"""from flask import Flask, request, jsonify
from wtpsplit import SaT

app = Flask(__name__)

sat = SaT("{sat_model}")
sat.half().to("cuda")

accepted_params = {{
    "text_or_texts": str,
    "threshold": float,
    "stride": int,
    "block_size": int,
    "batch_size": int,
    "pad_last_batch": bool,
    "weighting": str,
    "remove_whitespace_before_inference": bool,
    "outer_batch_size": int,
    "paragraph_threshold": float,
    "strip_whitespace": bool,
    "do_paragraph_segmentation": bool,
    "split_on_input_newlines": bool,
    "verbose": bool,
    "min_length": int,
    "max_length": int,
    "prior_type": str,
    "prior_kwargs": dict,
    "algorithm": str,
}}
def verify_params(params: dict) -> list:
    errors = []
    if "text_or_texts" not in params:
        errors.append(f"Missing key 'text_or_texts'.")
    for key, value in params.items():
        if key not in accepted_params:
            errors.append(f"Unknown key '{{key}}'.")
            continue
        expected_type = accepted_params[key]
        if type(value) is not expected_type:
            errors.append(
                f"Key '{{key}}' expects type '{{expected_type.__name__}}' "
                f"but received '{{type(value).__name__}}'."
            )
    return errors

@app.route("/ready", methods=["GET"])
def ready():
    return {{}}
    
@app.route("/split", methods=["POST"])
def split():
    params = request.get_json() or {{}}
    if errors := verify_params(params):
        print(errors)
        return jsonify({{"error": ", ".join(errors)}}), 400
    return jsonify({{"sentences": sat.split(**params)}})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
"""
    base64_server_script = base64.b64encode(server_script.encode()).decode()
    dockerfile = f"""
FROM rocm/pytorch:latest
RUN pip install flask gunicorn wtpsplit
RUN python -c "from wtpsplit import SaT; SaT('{sat_model}')"
RUN mkdir -p /app && echo {base64_server_script} | base64 -d > /app/wtpsplit_server.py
# ENTRYPOINT ["python", "/app/wtpsplit_server.py"]
WORKDIR /app
ENTRYPOINT ["gunicorn", "-w", "1", "--threads", "1", "--timeout", "120", "-b", "0.0.0.0:3000", "wtpsplit_server:app"]
"""
    dk.build_image("amd_wtpsplit:latest", dockerfile)


def run_wtpsplit_container(port: int, rocm_device: int = 0):
    return dk.run_container(
        "amd_wtpsplit:latest",
        {
            "detach": True,
            "devices": ["/dev/kfd", "/dev/dri"],
            "environment": {"ROCR_VISIBLE_DEVICES": rocm_device},
            "ports": {3000: port},
        },
    )


def stop_all_wtpsplit_containers():
    dk.stop_containers_by_tag("amd_wtpsplit:latest")
