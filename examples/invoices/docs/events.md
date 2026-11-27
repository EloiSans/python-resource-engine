# Esdeveniments publicats

Missatges que l'aplicació publica després de cada canvi confirmat.

## customers

Clients als quals l'organització emet factures.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | customers: a record was created. | `resource` = `customers`, `entity_id` |
| `ResourceUpdated` | customers: fields of a record changed. | `resource` = `customers`, `entity_id`, `changes` |
| `ResourceDeleted` | customers: a record was deleted. | `resource` = `customers`, `entity_id` |

## invoices

Gestió de factures de l'organització.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoices: a record was created. | `resource` = `invoices`, `entity_id` |
| `ResourceUpdated` | invoices: fields of a record changed. | `resource` = `invoices`, `entity_id`, `changes` |
| `ResourceDeleted` | invoices: a record was deleted. | `resource` = `invoices`, `entity_id` |
| `TransitionExecuted` | invoices: a record changed state. | `resource` = `invoices`, `entity_id`, `transition` (submit, approve, reject, pay, reopen, send, bounce, resend), `source` (draft, pending, approved, rejected, paid, unsent, sent, returned), `target` (draft, pending, approved, rejected, paid, unsent, sent, returned), `machine` (approval, delivery) |
| `ActionExecuted` | invoices: an action ran on a record. | `resource` = `invoices`, `entity_id`, `action` (recalculate_totals, render_summary) |
| `DocumentAttached` | invoices: a document was attached to a record. | `resource` = `invoices`, `entity_id`, `document_id`, `slot` (signed_pdf, attachments, summary), `content_type`, `size`, `processing` |
| `DocumentDetached` | invoices: a document of a record was removed. | `resource` = `invoices`, `entity_id`, `document_id`, `slot` (signed_pdf, attachments, summary) |
| `InvoiceTotalsRecalculated` | L'import d'una factura s'ha recalculat aplicant un descompte. | `invoice_id`, `previous_amount`, `new_amount` |

## tags

Etiquetes per classificar factures.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | tags: a record was created. | `resource` = `tags`, `entity_id` |
| `ResourceUpdated` | tags: fields of a record changed. | `resource` = `tags`, `entity_id`, `changes` |
| `ResourceDeleted` | tags: a record was deleted. | `resource` = `tags`, `entity_id` |

## invoice_tags

Etiquetes assignades a factures (molts a molts).

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoice_tags: a record was created. | `resource` = `invoice_tags`, `entity_id` |
| `ResourceDeleted` | invoice_tags: a record was deleted. | `resource` = `invoice_tags`, `entity_id` |

## invoice_payments

Cobrament de les factures aprovades.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoice_payments: a record was created. | `resource` = `invoice_payments`, `entity_id` |
| `TransitionExecuted` | invoice_payments: a record changed state. | `resource` = `invoice_payments`, `entity_id`, `transition` (receive, expire), `source` (awaiting, overdue, settled), `target` (awaiting, overdue, settled), `machine` (status) |
| `PaymentOverdue` | Una factura aprovada fa 15 dies que espera el cobrament. | `invoice_id` |

## authz_policies

Access policies (ABAC), managed from the front end.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_policies: a record was created. | `resource` = `authz_policies`, `entity_id` |
| `ResourceUpdated` | authz_policies: fields of a record changed. | `resource` = `authz_policies`, `entity_id`, `changes` |
| `ResourceDeleted` | authz_policies: a record was deleted. | `resource` = `authz_policies`, `entity_id` |
| `PolicyChanged` | Policies or role assignments changed: caches must be refreshed. | `resource`, `entity_id` |

## authz_roles

Custom roles, created from the front end. System roles are defined in code.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_roles: a record was created. | `resource` = `authz_roles`, `entity_id` |
| `ResourceUpdated` | authz_roles: fields of a record changed. | `resource` = `authz_roles`, `entity_id`, `changes` |
| `ResourceDeleted` | authz_roles: a record was deleted. | `resource` = `authz_roles`, `entity_id` |

## authz_role_assignments

Roles given to principals or groups from the front end (used by ``StoredRoleSource``).

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_role_assignments: a record was created. | `resource` = `authz_role_assignments`, `entity_id` |
| `ResourceDeleted` | authz_role_assignments: a record was deleted. | `resource` = `authz_role_assignments`, `entity_id` |
| `PolicyChanged` | Policies or role assignments changed: caches must be refreshed. | `resource`, `entity_id` |

## authz_api_keys

API keys for machine clients that cannot use OAuth.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_api_keys: a record was created. | `resource` = `authz_api_keys`, `entity_id` |
| `ResourceUpdated` | authz_api_keys: fields of a record changed. | `resource` = `authz_api_keys`, `entity_id`, `changes` |
| `TransitionExecuted` | authz_api_keys: a record changed state. | `resource` = `authz_api_keys`, `entity_id`, `transition` (revoke), `source` (active, revoked), `target` (active, revoked), `machine` (status) |

## Dades dels missatges

L'esquema JSON de cada missatge, a partir de [`asyncapi.json`](asyncapi.json).

### authz_api_keys.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_api_keys"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_api_keys"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_api_keys.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_api_keys"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_api_keys"
        },
        "entity_id": {
          "type": "string"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_api_keys.TransitionExecuted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "TransitionExecuted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_api_keys"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_api_keys"
        },
        "entity_id": {
          "type": "string"
        },
        "transition": {
          "enum": [
            "revoke"
          ]
        },
        "source": {
          "enum": [
            "active",
            "revoked"
          ]
        },
        "target": {
          "enum": [
            "active",
            "revoked"
          ]
        },
        "machine": {
          "enum": [
            "status"
          ]
        }
      },
      "required": [
        "resource",
        "entity_id",
        "transition",
        "source",
        "target",
        "machine"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_policies.PolicyChanged

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "PolicyChanged"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_policies"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "description": "Policies or role assignments changed: caches must be refreshed.",
      "properties": {
        "resource": {
          "title": "Resource",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ],
      "type": "object"
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_policies.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_policies"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_policies"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_policies.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_policies"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_policies"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_policies.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_policies"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_policies"
        },
        "entity_id": {
          "type": "string"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_role_assignments.PolicyChanged

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "PolicyChanged"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_role_assignments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "description": "Policies or role assignments changed: caches must be refreshed.",
      "properties": {
        "resource": {
          "title": "Resource",
          "type": "string"
        },
        "entity_id": {
          "title": "Entity Id"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ],
      "type": "object"
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_role_assignments.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_role_assignments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_role_assignments"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_role_assignments.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_role_assignments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_role_assignments"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_roles.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_roles"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_roles"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_roles.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_roles"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_roles"
        },
        "entity_id": {
          "type": "string"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### authz_roles.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "authz_roles"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "authz_roles"
        },
        "entity_id": {
          "type": "string"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### customers.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "customers"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "customers"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### customers.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "customers"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "customers"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### customers.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "customers"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "customers"
        },
        "entity_id": {
          "type": "integer"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoice_payments.PaymentOverdue

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "PaymentOverdue"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoice_payments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "description": "Una factura aprovada fa 15 dies que espera el cobrament.",
      "properties": {
        "invoice_id": {
          "title": "Invoice Id",
          "type": "integer"
        }
      },
      "required": [
        "invoice_id"
      ],
      "type": "object"
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoice_payments.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoice_payments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoice_payments"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoice_payments.TransitionExecuted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "TransitionExecuted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoice_payments"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoice_payments"
        },
        "entity_id": {
          "type": "integer"
        },
        "transition": {
          "enum": [
            "receive",
            "expire"
          ]
        },
        "source": {
          "enum": [
            "awaiting",
            "overdue",
            "settled"
          ]
        },
        "target": {
          "enum": [
            "awaiting",
            "overdue",
            "settled"
          ]
        },
        "machine": {
          "enum": [
            "status"
          ]
        }
      },
      "required": [
        "resource",
        "entity_id",
        "transition",
        "source",
        "target",
        "machine"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoice_tags.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoice_tags"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoice_tags"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoice_tags.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoice_tags"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoice_tags"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.ActionExecuted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ActionExecuted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        },
        "action": {
          "enum": [
            "recalculate_totals",
            "render_summary"
          ]
        }
      },
      "required": [
        "resource",
        "entity_id",
        "action"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.DocumentAttached

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "DocumentAttached"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        },
        "document_id": {
          "type": "string"
        },
        "slot": {
          "enum": [
            "signed_pdf",
            "attachments",
            "summary"
          ]
        },
        "content_type": {
          "type": "string"
        },
        "size": {
          "type": [
            "integer",
            "null"
          ]
        },
        "processing": {
          "type": "boolean"
        }
      },
      "required": [
        "resource",
        "entity_id",
        "document_id",
        "slot",
        "content_type",
        "size",
        "processing"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.DocumentDetached

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "DocumentDetached"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        },
        "document_id": {
          "type": "string"
        },
        "slot": {
          "enum": [
            "signed_pdf",
            "attachments",
            "summary"
          ]
        }
      },
      "required": [
        "resource",
        "entity_id",
        "document_id",
        "slot"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.InvoiceTotalsRecalculated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "InvoiceTotalsRecalculated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "description": "L'import d'una factura s'ha recalculat aplicant un descompte.",
      "properties": {
        "invoice_id": {
          "title": "Invoice Id",
          "type": "integer"
        },
        "previous_amount": {
          "title": "Previous Amount",
          "type": "string"
        },
        "new_amount": {
          "title": "New Amount",
          "type": "string"
        }
      },
      "required": [
        "invoice_id",
        "previous_amount",
        "new_amount"
      ],
      "type": "object"
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### invoices.TransitionExecuted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "TransitionExecuted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "invoices"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "invoices"
        },
        "entity_id": {
          "type": "integer"
        },
        "transition": {
          "enum": [
            "submit",
            "approve",
            "reject",
            "pay",
            "reopen",
            "send",
            "bounce",
            "resend"
          ]
        },
        "source": {
          "enum": [
            "draft",
            "pending",
            "approved",
            "rejected",
            "paid",
            "unsent",
            "sent",
            "returned"
          ]
        },
        "target": {
          "enum": [
            "draft",
            "pending",
            "approved",
            "rejected",
            "paid",
            "unsent",
            "sent",
            "returned"
          ]
        },
        "machine": {
          "enum": [
            "approval",
            "delivery"
          ]
        }
      },
      "required": [
        "resource",
        "entity_id",
        "transition",
        "source",
        "target",
        "machine"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### tags.ResourceCreated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceCreated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "tags"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "tags"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### tags.ResourceDeleted

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceDeleted"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "tags"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "tags"
        },
        "entity_id": {
          "type": "integer"
        }
      },
      "required": [
        "resource",
        "entity_id"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```

### tags.ResourceUpdated

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique message id"
    },
    "type": {
      "const": "ResourceUpdated"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "correlation_id": {
      "type": [
        "string",
        "null"
      ],
      "description": "Id of the operation that produced the event"
    },
    "resource": {
      "const": "tags"
    },
    "key": {
      "type": [
        "string",
        "null"
      ],
      "description": "Ordering key: <resource>:<entity_id>"
    },
    "schema_version": {
      "const": "1"
    },
    "payload": {
      "type": "object",
      "properties": {
        "resource": {
          "const": "tags"
        },
        "entity_id": {
          "type": "integer"
        },
        "changes": {
          "type": "object",
          "description": "Changed fields: [old value, new value]",
          "additionalProperties": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2
          }
        }
      },
      "required": [
        "resource",
        "entity_id",
        "changes"
      ]
    },
    "trace": {
      "type": "object",
      "description": "W3C trace context of the producing operation",
      "properties": {
        "traceparent": {
          "type": "string"
        },
        "tracestate": {
          "type": "string"
        }
      }
    }
  },
  "required": [
    "id",
    "type",
    "occurred_at",
    "schema_version",
    "payload"
  ]
}
```
