# Security

## Never commit credentials

AgriView expects the Google Maps Platform credential through:

```text
GOOGLE_MAPS_API_KEY
```

Do not commit `.env`, API keys, service-account files, production imagery, metadata exports, or
private field-boundary data.

## Exposed-key response

If a key is accidentally committed:

1. revoke or rotate it immediately;
2. restrict the replacement key by API and application;
3. remove the credential from Git history;
4. review billing and access logs.

Use GitHub private vulnerability reporting for security concerns when available.
