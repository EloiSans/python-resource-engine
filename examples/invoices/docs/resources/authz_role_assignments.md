# authz_role_assignments

Roles given to principals or groups from the front end (used by

``StoredRoleSource``).

## Àmbit de negoci

Permissions

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Lists role assignments, filterable by role, principal or group. |
| `get` | crud | Returns a role assignment. |
| `create` | crud | Gives a role to a principal or a group. |
| `delete` | crud | Removes a role assignment. |

Auditoria: s'auditen els canvis i els intents denegats.

## Esdeveniments de domini

### `PolicyChanged`

Policies or role assignments changed: caches must be refreshed.

| Camp | Tipus | Obligatori |
|---|---|---|
| `resource` | `str` | sí |
| `entity_id` | `Any` | sí |

## Què passa en cada operació

Els passos **abans de confirmar** són dins la transacció: si un falla, el canvi es rebutja i no es desa res. Els passos **després de confirmar** s'executen amb el canvi ja desat: un error queda registrat, però no el desfà.

### Crear (`create`)

1. Es valida l'entrada.
2. Abans de desar: Exactly one of principal or group; the role exists; the editor holds every
3. Es desa el registre nou.
4. Abans de confirmar: Emits ``PolicyChanged`` so every instance refreshes its permission caches.
5. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["Validar"]
    s1["Exactly one of principal or group; the…"]
    s0 --> s1
    s2["Desar"]
    s1 --> s2
    s3["Emits ``PolicyChanged`` so every instan…"]
    s2 --> s3
    s4{{"Confirmar"}}
    s3 --> s4
```

### Eliminar (`delete`)

1. S'elimina el registre.
2. Abans de confirmar: Emits ``PolicyChanged`` so every instance refreshes its permission caches.
3. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["Eliminar"]
    s1["Emits ``PolicyChanged`` so every instan…"]
    s0 --> s1
    s2{{"Confirmar"}}
    s1 --> s2
```

Després de qualsevol canvi confirmat d'aquest recurs:

- Refreshes this instance's policy and role caches after the commit.
