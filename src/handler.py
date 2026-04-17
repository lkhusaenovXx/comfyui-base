"""
RunPod Serverless handler for ComfyUI.

Accepts a ComfyUI workflow JSON as input, submits it to the local ComfyUI
instance, waits for completion, and returns generated images as base64.

Input format:
    {"workflow": { ... ComfyUI API-format workflow ... }}

Output format:
    {"images": [{"image": "<base64>", "node_id": "...", "filename": "..."}], "prompt_id": "..."}
"""

import base64
import json
import os
import time
import urllib.parse
import urllib.request

import runpod

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
TIMEOUT = int(os.environ.get("COMFYUI_TIMEOUT", "300"))


def wait_for_comfyui(url, retries=120, delay=1.0):
    """Block until ComfyUI's HTTP server is reachable."""
    for _ in range(retries):
        try:
            urllib.request.urlopen(f"{url}/system_stats", timeout=5)
            return True
        except Exception:
            time.sleep(delay)
    return False


def queue_prompt(prompt):
    data = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def get_history(prompt_id):
    resp = urllib.request.urlopen(f"{COMFYUI_URL}/history/{prompt_id}")
    return json.loads(resp.read())


def get_image(filename, subfolder, folder_type):
    params = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": folder_type}
    )
    resp = urllib.request.urlopen(f"{COMFYUI_URL}/view?{params}")
    return resp.read()


def handler(job):
    job_input = job["input"]
    workflow = job_input.get("workflow")

    if not workflow:
        return {"error": "No 'workflow' provided in input"}

    # Queue the workflow
    try:
        result = queue_prompt(workflow)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"ComfyUI rejected the prompt: {body}"}
    except Exception as e:
        return {"error": f"Failed to queue prompt: {str(e)}"}

    prompt_id = result.get("prompt_id")
    if not prompt_id:
        return {"error": "No prompt_id returned", "details": result}

    # Poll for completion
    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        history = get_history(prompt_id)
        if prompt_id in history:
            status = history[prompt_id].get("status", {})
            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                return {"error": "Workflow execution failed", "details": msgs}
            if "outputs" in history[prompt_id]:
                break
        time.sleep(0.5)
    else:
        return {"error": f"Timeout after {TIMEOUT}s waiting for workflow to complete"}

    # Collect output images
    outputs = history[prompt_id].get("outputs", {})
    images = []
    for node_id, output in outputs.items():
        if "images" in output:
            for img in output["images"]:
                img_data = get_image(
                    img["filename"], img.get("subfolder", ""), img["type"]
                )
                images.append(
                    {
                        "image": base64.b64encode(img_data).decode("utf-8"),
                        "node_id": node_id,
                        "filename": img["filename"],
                    }
                )

    return {"images": images, "prompt_id": prompt_id}


if __name__ == "__main__":
    print("Waiting for ComfyUI to become available...")
    if not wait_for_comfyui(COMFYUI_URL):
        raise RuntimeError("ComfyUI did not start in time")
    print("ComfyUI is ready. Starting RunPod serverless handler...")
    runpod.serverless.start({"handler": handler})