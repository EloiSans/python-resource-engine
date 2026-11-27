# Correus

Els correus que envia l'aplicació, a partir de les plantilles. Les variables es mostren com a `{{ nom }}`: el contingut real depèn de cada cas.

## invoice_approved

Enviat per [invoices](resources/invoices.md) en la transició `approve`.

### ca

**Assumpte:** Factura {{ number }} aprovada

```text
Hola, {{ customer }}:

La factura {{ number }}, de {{ amount }} €, ha estat aprovada.

{{ app_name }}
```

```email-html
<!doctype html>
<html lang="ca">
<body style="font-family: system-ui, sans-serif; color: #1f2328; max-width: 36rem">
<p>Hola, <mark class="placeholder">{{ customer }}</mark>:</p>
<p>La factura <strong><mark class="placeholder">{{ number }}</mark></strong>, de <strong><mark class="placeholder">{{ amount }}</mark> €</strong>, ha estat aprovada.</p>
<p style="color: #59636e; font-size: 0.85em"><mark class="placeholder">{{ app_name }}</mark></p>
</body>
</html>
```

### en

**Assumpte:** Invoice {{ number }} approved

```text
Hello {{ customer }},

Invoice {{ number }}, for {{ amount }} €, has been approved.

{{ app_name }}
```

```email-html
<!doctype html>
<html lang="en">
<body style="font-family: system-ui, sans-serif; color: #1f2328; max-width: 36rem">
<p>Hello <mark class="placeholder">{{ customer }}</mark>,</p>
<p>Invoice <strong><mark class="placeholder">{{ number }}</mark></strong>, for <strong><mark class="placeholder">{{ amount }}</mark> €</strong>, has been approved.</p>
<p style="color: #59636e; font-size: 0.85em"><mark class="placeholder">{{ app_name }}</mark></p>
</body>
</html>
```

### es

**Assumpte:** Factura {{ number }} aprobada

```text
Hola, {{ customer }}:

La factura {{ number }}, de {{ amount }} €, ha sido aprobada.

{{ app_name }}
```

```email-html
<!doctype html>
<html lang="es">
<body style="font-family: system-ui, sans-serif; color: #1f2328; max-width: 36rem">
<p>Hola, <mark class="placeholder">{{ customer }}</mark>:</p>
<p>La factura <strong><mark class="placeholder">{{ number }}</mark></strong>, de <strong><mark class="placeholder">{{ amount }}</mark> €</strong>, ha sido aprobada.</p>
<p style="color: #59636e; font-size: 0.85em"><mark class="placeholder">{{ app_name }}</mark></p>
</body>
</html>
```
