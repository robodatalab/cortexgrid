# SES domain identity for robodatalab.com — verifies all @robodatalab.com addresses.
# DKIM records are added to Route53 automatically.
# After apply, SES sandbox still restricts recipients to verified addresses.
# Request production access in the AWS Console (SES > Account dashboard > Request production access)
# to lift that restriction and allow sending to any email.

resource "aws_ses_domain_identity" "domain" {
  domain = var.domain_name
}

resource "aws_ses_domain_dkim" "domain" {
  domain = aws_ses_domain_identity.domain.domain
}

# Domain verification TXT record
resource "aws_route53_record" "ses_verification" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "_amazonses.${var.domain_name}"
  type    = "TXT"
  ttl     = 600
  records = [aws_ses_domain_identity.domain.verification_token]
}

# DKIM CNAME records (3 of them)
resource "aws_route53_record" "ses_dkim" {
  count   = 3
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "${aws_ses_domain_dkim.domain.dkim_tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_ses_domain_dkim.domain.dkim_tokens[count.index]}.dkim.amazonses.com"]
}

# Verify the admin recipient address so it works while SES is in sandbox mode.
# Once production access is granted this resource can be removed.
resource "aws_ses_email_identity" "admin" {
  email = var.admin_email
}
