# Platform System Configuration Guide

This document provides comprehensive reference documentation for configuring the Meridian platform's system-level settings. All configuration is managed through YAML files located in the `/etc/meridian/` directory on each node in the cluster. Changes to system configuration require a service restart to take effect unless otherwise noted.

## System Configuration

The primary configuration file is `meridian.yaml`, located at `/etc/meridian/meridian.yaml`. This file controls core platform behaviour including service discovery, logging, authentication, and resource allocation. A minimal configuration file must specify the node role, cluster membership, and data directory. All other settings have sensible defaults that are appropriate for development and small-scale deployments.

```yaml
system:
  enabled: true
  node_role: primary
  cluster_name: production-east
  data_dir: /var/lib/meridian/data
  log_level: info
  max_connections: 500
```

The `system: enabled` flag must be set to `true` for the service to start. Setting it to `false` causes the process to exit immediately after loading configuration, which is useful for validating configuration syntax without actually starting the service.

## Admin Configuration

Platform administration is controlled through a dedicated section of the configuration file. The admin interface provides access to cluster management, user provisioning, and diagnostic tools. Access to admin functions is restricted to users with the `platform-admin` role.

```yaml
admin:
  enabled: true
  listen_address: 127.0.0.1
  port: 9443
  tls:
    cert_file: /etc/meridian/certs/admin.crt
    key_file: /etc/meridian/certs/admin.key
  auth:
    method: oidc
    issuer: https://auth.example.com
    required_role: platform-admin
```

Setting `admin: true` in abbreviated configuration mode (used for Docker deployments) enables the admin interface with default settings. In production, always use the expanded form shown above to explicitly configure TLS certificates and authentication parameters.

## SYSTEM: Prefix in Log Output

The platform uses structured log prefixes to categorise messages by subsystem. Each log line begins with a prefix indicating its source. The most common prefixes are:

```
SYSTEM: Service started on port 8443
SYSTEM: Configuration loaded from /etc/meridian/meridian.yaml
ADMIN: User provisioning API initialised
ADMIN: TLS certificate expires in 42 days
WORKER: Job queue connected to redis://localhost:6379
WORKER: Processing backlog of 1,247 pending tasks
```

These prefixes are purely informational and are used by log aggregation tools to route messages to the appropriate dashboards and alert rules. The prefix format is configurable through the `log_format.prefix_style` setting, which accepts values of `uppercase`, `lowercase`, or `none`.

## Service Discovery

Meridian nodes discover each other through a gossip-based protocol that operates over UDP. Each node periodically broadcasts its identity, role, and health status to other nodes in the cluster. The discovery configuration controls the network parameters of this protocol.

```yaml
discovery:
  protocol: gossip
  bind_address: 0.0.0.0
  bind_port: 7946
  join_addresses:
    - 10.0.1.10:7946
    - 10.0.1.11:7946
    - 10.0.1.12:7946
  gossip_interval: 1s
  probe_timeout: 500ms
  suspicion_multiplier: 4
```

When a new node joins the cluster, it contacts one or more seed nodes listed in `join_addresses` to obtain the current cluster membership list. The gossip protocol then propagates the new node's presence to all other members within a few seconds, depending on cluster size and network conditions.

## Resource Limits

Each node enforces resource limits to prevent any single tenant or workload from consuming disproportionate resources. These limits are configured per-node and can be overridden on a per-tenant basis through the admin API.

```yaml
resources:
  max_cpu_percent: 80
  max_memory_mb: 16384
  max_disk_io_mbps: 500
  max_network_mbps: 1000
  per_tenant:
    default:
      max_connections: 100
      max_queries_per_second: 50
      max_storage_gb: 100
```

The resource limits subsystem monitors actual usage continuously and begins throttling workloads when they approach their configured limits. Throttling is gradual rather than abrupt: as a workload approaches its limit, its requests are progressively delayed rather than rejected, providing back-pressure that naturally reduces consumption without causing errors.

## Backup and Recovery

The platform includes built-in backup capabilities that produce consistent point-in-time snapshots of all data. Backups can be stored locally, on network-attached storage, or in object storage services such as Amazon S3 or Google Cloud Storage.

```yaml
backup:
  schedule: "0 2 * * *"
  retention_days: 30
  storage:
    type: s3
    bucket: meridian-backups-prod
    region: us-east-1
    prefix: daily/
  encryption:
    enabled: true
    key_id: arn:aws:kms:us-east-1:123456789:key/abcd-1234
```

Recovery from a backup requires stopping the service, restoring the data directory from the backup archive, and restarting the service. The platform automatically detects that the data directory was restored from a backup and performs consistency checks before accepting connections.

## Monitoring and Alerting

The platform exposes metrics in Prometheus format on a dedicated metrics endpoint. The default configuration exports approximately 200 metrics covering request latency, throughput, error rates, resource utilisation, and internal queue depths.

```yaml
monitoring:
  metrics:
    enabled: true
    port: 9090
    path: /metrics
  health_check:
    port: 8080
    path: /health
    interval: 10s
```

Integration with alerting systems is handled externally through Prometheus alerting rules or compatible monitoring platforms. The platform's documentation includes a set of recommended alerting rules that cover the most common operational scenarios, including node unavailability, elevated error rates, and approaching resource limits.
