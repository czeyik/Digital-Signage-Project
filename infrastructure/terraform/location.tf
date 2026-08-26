# Amazon Location Maps v2 serves the GrabMaps vector.basemap tiles in the
# production ap-southeast-5 region. The browser key is deliberately restricted
# to read-only map actions and the dashboard referrer.
resource "awscc_location_api_key" "fleet_map" {
  provider    = awscc
  key_name    = "${local.name}-fleet-map"
  description = "Read-only GrabMaps vector.basemap key for the fleet dashboard"
  no_expiry   = true

  restrictions = {
    allow_actions = [
      "geo-maps:GetStyleDescriptor",
      "geo-maps:GetTile",
      "geo-maps:GetGlyphs",
      "geo-maps:GetSprites",
    ]
    allow_resources = [
      "arn:aws:geo-maps:${var.aws_region}::provider/*",
    ]
    allow_referers = ["https://${var.dashboard_hostname}/*"]
  }

  tags = [
    { key = "Component", value = "fleet-location-map" },
    { key = "CostCenter", value = "pilot-maps" },
  ]
}
