# Labelable Python Client Integration

This document describes how to integrate with the Labelable API from Python applications.

## Base URL

- **Home Assistant Add-on**: `http://local-labelable:7979` (internal Docker network)
- **Direct access**: `http://<host>:7979`

## Authentication

If `api_key` is configured in Labelable's `config.yaml`, include it in requests:

```python
headers = {"X-API-Key": "your-secret-key"}
# or
headers = {"Authorization": "Bearer your-secret-key"}
```

## API Endpoints

### List Printers

```python
GET /api/v1/printers
```

**Response** (`200 OK`):
```python
[
    {
        "name": "warehouse-zpl",
        "type": "zpl",  # or "epl2"
        "online": True,
        "queue_size": 0
    }
]
```

### Get Printer Status

```python
GET /api/v1/printers/{name}
```

**Response** (`200 OK`):
```python
{
    "name": "warehouse-zpl",
    "type": "zpl",
    "online": True,
    "queue_size": 0
}
```

### List Templates

```python
GET /api/v1/templates
```

**Response** (`200 OK`):
```python
[
    {
        "name": "shipping-label",
        "description": "Basic shipping address label",
        "width_mm": 100.0,
        "height_mm": 50.0,
        "supported_printers": ["warehouse-zpl"],
        "fields": [
            {
                "name": "recipient",
                "type": "string",
                "required": True,
                "default": None,
                "description": "Recipient name",
                "format": "",
                "options": []
            }
        ]
    }
]
```

### Get Template Details

```python
GET /api/v1/templates/{name}
```

**Response** (`200 OK`): Same structure as list item above.

### Print Label

```python
POST /api/v1/print/{template_name}
Content-Type: application/json

{
    "printer": "warehouse-zpl",  # optional - uses first compatible if omitted
    "quantity": 1,
    "data": {
        "recipient": "John Doe",
        "address": "123 Main St"
    }
}
```

**Response** (`200 OK` - printed):
```python
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "message": "Label submitted for printing"
}
```

**Response** (`202 Accepted` - queued because printer offline):
```python
{
    "detail": {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "status": "pending",
        "message": "Label queued - printer 'warehouse-zpl' is offline"
    }
}
```

### Get Job Status

```python
GET /api/v1/jobs/{job_id}
```

**Response** (`200 OK`):
```python
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "completed",  # pending, printing, completed, failed, expired
    "message": "Job status: completed"
}
```

## Field Types

Templates can define these field types:

| Type | Description | Auto-populated |
|------|-------------|----------------|
| `string` | Text input | No |
| `integer` | Whole number | No |
| `float` | Decimal number | No |
| `boolean` | True/false | No |
| `select` | Choice from `options` list | No |
| `datetime` | Current timestamp | Yes (uses `format` for strftime) |
| `user` | Current user name | Yes (from HA user mapping) |

Auto-populated fields don't need to be included in request `data`.

## Complete Python Example

```python
import requests

BASE_URL = "http://localhost:7979"
API_KEY = "your-secret-key"  # omit if not configured

def get_headers():
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers

def list_printers():
    """Get all printers with their status."""
    response = requests.get(f"{BASE_URL}/api/v1/printers", headers=get_headers())
    response.raise_for_status()
    return response.json()

def list_templates():
    """Get all available templates."""
    response = requests.get(f"{BASE_URL}/api/v1/templates", headers=get_headers())
    response.raise_for_status()
    return response.json()

def print_label(template_name: str, data: dict, printer: str = None, quantity: int = 1):
    """Print a label.

    Args:
        template_name: Name of the template to use
        data: Dictionary of field values for the template
        printer: Printer name (optional - uses first compatible if omitted)
        quantity: Number of labels to print

    Returns:
        dict with job_id, status, message

    Raises:
        requests.HTTPError: On API errors (4xx, 5xx)
    """
    payload = {
        "data": data,
        "quantity": quantity,
    }
    if printer:
        payload["printer"] = printer

    response = requests.post(
        f"{BASE_URL}/api/v1/print/{template_name}",
        json=payload,
        headers=get_headers(),
    )

    # 202 means queued (printer offline) - still successful
    if response.status_code == 202:
        return response.json()["detail"]

    response.raise_for_status()
    return response.json()

def get_job_status(job_id: str):
    """Check status of a print job."""
    response = requests.get(f"{BASE_URL}/api/v1/jobs/{job_id}", headers=get_headers())
    response.raise_for_status()
    return response.json()

# Usage example
if __name__ == "__main__":
    # Check printers
    printers = list_printers()
    print(f"Available printers: {[p['name'] for p in printers]}")

    # Check templates
    templates = list_templates()
    print(f"Available templates: {[t['name'] for t in templates]}")

    # Print a label
    result = print_label(
        template_name="shipping-label",
        data={
            "recipient": "John Doe",
            "address": "123 Main Street",
        },
        quantity=2,
    )
    print(f"Print job: {result['job_id']} - {result['message']}")
```

## Error Handling

| Status Code | Meaning |
|-------------|---------|
| `200` | Success - label sent to printer |
| `202` | Accepted - label queued (printer offline) |
| `400` | Bad request - invalid data or incompatible printer |
| `401` | Unauthorized - invalid or missing API key |
| `404` | Not found - template or printer doesn't exist |

## Home Assistant Automation Example

When running as an add-on, use the internal hostname:

```yaml
# configuration.yaml
rest_command:
  print_label:
    url: "http://local-labelable:7979/api/v1/print/{{ template }}"
    method: POST
    content_type: "application/json"
    payload: '{"data": {{ data | tojson }}, "printer": "{{ printer }}", "quantity": {{ quantity | default(1) }}}'
```

```yaml
# automation
action:
  - service: rest_command.print_label
    data:
      template: shipping-label
      printer: warehouse-zpl
      data:
        recipient: "{{ trigger.event.data.name }}"
        address: "{{ trigger.event.data.address }}"
      quantity: 1
```
