variable "project_id" { type = string }
variable "region" {
  type = string
  default = "us-central1"
}
variable "dataset_id" {
  type = string
  default = "trajectory"
}
# The single highest-leverage cost guardrail in the build. 100 PU is the Spanner
# minimum and ample for ~200k edges. Provisioned as a full node (1000 PU) this
# is 10x - about $151/attendee/week instead of $15. Do not raise it "to be safe".
variable "spanner_processing_units" {
  type    = number
  default = 100
  validation {
    condition     = var.spanner_processing_units <= 200
    error_message = "Refusing >200 PU: see WORKSHOP-PLAN.md 8.5. 100 PU is ample for the T2 subset."
  }
}

variable "enable_streaming" {
  type = bool
  default = true
}
variable "enable_gke" {
  type = bool
  default = true
}
variable "labels" {
  type    = map(string)
  default = { workshop = "agent-trajectory-monitoring" }
}
