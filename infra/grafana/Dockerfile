FROM grafana/grafana:11.1.0

# AWS-specific datasource configs (Cloud Map DNS instead of docker-compose hostnames)
COPY provisioning-aws/datasources /etc/grafana/provisioning/datasources

# Reuse the same dashboard JSON from local provisioning
COPY provisioning/dashboards /etc/grafana/provisioning/dashboards
