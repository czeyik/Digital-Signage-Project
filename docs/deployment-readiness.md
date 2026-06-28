# Deployment Readiness

Run the readiness check before deploying production:

```sh
python manage.py check_deployment_readiness --environment production
```

The production check verifies that debug mode is off, PostgreSQL is configured,
private object storage is configured, production hostnames are present, secure
cookies and HTTPS redirect are enabled, and media-processing tools are
available. It also warns if email is still console-only.

Development and production must use separate databases, buckets, secrets,
credentials, enrollment codes, backup roots, and device identities. Set
`DEPLOYMENT_ENV` explicitly in each environment and never reuse production
credentials locally.
