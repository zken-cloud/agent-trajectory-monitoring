variable "project_id" { type = string }
variable "dataset_id" { type = string }

# Rebuilds on GKE what Agent Engine handed us for free in Scenario 1. The point
# is not that it is hard - it is that reproducing the managed dashboard is
# CHEAP, and it was never the security layer. What is not reproducible from a
# dashboard is everything in the DETECT lane.
resource "google_monitoring_dashboard" "agent_ops" {
  dashboard_json = templatefile("${path.module}/agent_ops_dashboard.json", {
    project_id = var.project_id
    dataset_id = var.dataset_id
  })
}

# The Looker Studio detection dashboard is NOT Terraform-managed: it is a
# copy-and-repoint template link. That is deliberate - it is the same motion an
# attendee uses to hand it to a customer. See LAB-GUIDE.md 2.4.1.
# Emitting a half-built URL here was worse than emitting none: it named only
# ds0 and no connector, so it opened a report with nothing bound to it. The
# link needs all four views spelled out - see labs/looker_link.sh, which builds
# both the copy link and the create-from-blank fallback.
output "looker_studio_link_command" {
  value = "GOOGLE_CLOUD_PROJECT=${var.project_id} TRAJECTORY_DATASET=${var.dataset_id} bash labs/looker_link.sh"
}
