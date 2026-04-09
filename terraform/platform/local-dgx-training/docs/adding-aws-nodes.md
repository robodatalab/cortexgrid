# Adding AWS EC2 Nodes

Scale the Ray cluster by adding GPU-equipped EC2 instances as worker nodes. The workers join the existing DGX Spark head node over Tailscale — no changes to your code or infrastructure config.

## Recommended Instance Types

| Instance     | GPUs          | VRAM    | Use Case                     |
|-------------|---------------|---------|------------------------------|
| g5.xlarge   | 1x A10G       | 24 GB   | Fine-tuning, evaluation      |
| g5.2xlarge  | 1x A10G       | 24 GB   | Same + more CPU/RAM          |
| g5.12xlarge | 4x A10G       | 96 GB   | Multi-GPU training           |
| p4d.24xlarge| 8x A100       | 320 GB  | Large model training         |

## Step-by-Step

### 1. Launch EC2 Instance

- AMI: Deep Learning AMI (Ubuntu) — comes with NVIDIA drivers pre-installed
- Security group: allow Tailscale UDP (port 41641) outbound; all inbound from Tailscale CIDR
- Storage: 100+ GB gp3

### 2. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Verify the DGX is reachable:
```bash
ping <DGX_TAILSCALE_IP>
```

### 3. Install NVIDIA Drivers (if not using Deep Learning AMI)

```bash
sudo apt-get update
sudo apt-get install -y nvidia-driver-535
sudo nvidia-smi  # verify
```

### 4. Install Docker + NVIDIA Container Toolkit

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# NVIDIA Container Toolkit
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 5. Join the Ray Cluster

From your Mac, get the join command:
```bash
cd terraform/platform/local-dgx-training
bash scripts/add-aws-node.sh
```

Then run the printed command on the EC2 instance:
```bash
pip install "ray[default]==2.9.3"
ray start --address=<DGX_TAILSCALE_IP>:6379 --num-gpus=<N> --block
```

Or run as a Docker container for automatic restarts:
```bash
docker run -d --gpus all --network host \
    --name ray-worker \
    --restart unless-stopped \
    rayproject/ray:2.9.3-gpu \
    ray start --address=<DGX_TAILSCALE_IP>:6379 --num-gpus=<N> --block
```

### 6. Verify

- Open the Ray Dashboard: `http://<DGX_TAILSCALE_IP>:8265`
- The new node should appear under "Nodes" with its GPU resources
- Submit a job — Ray will automatically schedule tasks across all available nodes

## Notes

- **No changes to job scripts.** Ray distributes tasks across all registered nodes automatically.
- **No changes to docker-compose.yml.** Workers connect to the head node directly.
- **Storage access:** EC2 workers need access to MinIO/S3 for checkpoints. When using MinIO on the DGX, ensure port 9000 is reachable via Tailscale (it is by default). When using real S3, workers use their instance role.
- **Teardown:** Run `ray stop` on the EC2 instance to gracefully leave the cluster.
