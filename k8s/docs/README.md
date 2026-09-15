# Docs site on Kubernetes

Build the static site image from the repo root:

```bash
docker build -f Dockerfile.docs -t jarvis_docs:latest .
kubectl apply -k k8s/docs
```

For production, push `jarvis_docs` to your registry and set the image on the Deployment.
