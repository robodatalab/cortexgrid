data "aws_route53_zone" "main" {
  name = var.domain_name
}

resource "aws_route53_record" "tailnet" {
  for_each = toset(var.hostnames)

  zone_id = data.aws_route53_zone.main.zone_id
  name    = "${each.key}.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  records = ["robolab-head.${var.tailnet}"]
}
