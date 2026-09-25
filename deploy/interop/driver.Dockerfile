# The interop test driver: the script baked in (CI runners can't bind-mount the workspace
# into sibling containers).
FROM python:3.13-slim
COPY scripts/interop/gotosocial_interop.py /interop/gotosocial_interop.py
CMD ["python", "/interop/gotosocial_interop.py"]
