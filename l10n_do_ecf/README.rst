Facturación Electrónica (e-CF) — GAE · República Dominicana
=============================================================

Integración nativa de **Odoo 17** con el **Gestor de Autorizaciones Electrónicas (GAE)**
de la DGII para la emisión de Comprobantes Fiscales Electrónicos (e-CF) bajo la Ley 32-23.

Características
---------------

* Envío de e-CF al GAE con un clic desde la factura confirmada
* Soporte completo para E31, E32, E33, E34, E41, E43, E44, E45, E46 y E47
* Gestión de estados: Pendiente → Enviado → Aprobado / Rechazado
* Obtención automática del timbre digital (código de seguridad, URL DGII, fecha de firma)
* Código QR DGII impreso en la representación física del e-CF
* Impuestos adicionales: ISC, propina legal (10%), telecomunicaciones (2%)
* Retenciones ITBIS e ISR para comprobantes de compras (E41/E47)
* Notas de crédito (E34) y débito (E33) con código de modificación
* Soporte para facturas en moneda extranjera (tasa de cambio automática)
* Panel de configuración integrado en Ajustes de Odoo

Requisitos Previos
------------------

Antes de usar este módulo el negocio debe contar con:

* RNC activo ante la DGII
* Certificado de firma digital vigente
* Certificación como emisor electrónico (DGII)
* Secuencias de e-CF autorizadas cargadas en el GAE
* Cuenta activa en `GAE Digital <https://gaedigital.com>`_ con API Key

Dependencias Python::

    pip install qrcode requests

Configuración
-------------

1. Instalar el módulo desde Apps de Odoo.
2. Ir a **Ajustes → Facturación Electrónica (eCF)**.
3. Activar **"Es emisor de e-CF"**.
4. Ingresar la **API Key**, **URL del GAE** y **Código de punto de venta**.
5. Seleccionar el entorno (**Test** o **Producción**).
6. En los diarios contables, asignar tipos de documento electrónico (E31, E32, etc.).

Flujo de Emisión
----------------

1. Confirmar factura con tipo de documento e-CF (ej. E31).
2. Hacer clic en **Enviar a DGII (eCF)** en la factura.
3. El módulo construye y envía el payload al GAE.
4. El estado cambia a **Enviado** (el GAE procesa en lote).
5. Hacer clic en **Verificar Estado GAE** para obtener el resultado.
6. Si es aprobado: código de seguridad y QR se guardan y aparecen en el PDF.

Tipos de Comprobante Soportados
--------------------------------

+-----+---------------------------------------+
| E31 | Factura de Crédito Fiscal             |
+-----+---------------------------------------+
| E32 | Factura de Consumo                    |
+-----+---------------------------------------+
| E33 | Nota de Débito                        |
+-----+---------------------------------------+
| E34 | Nota de Crédito                       |
+-----+---------------------------------------+
| E41 | Comprobante de Compras (retenciones)  |
+-----+---------------------------------------+
| E43 | Gasto Menor                           |
+-----+---------------------------------------+
| E44 | Régimen Especial de Tributación       |
+-----+---------------------------------------+
| E45 | Gubernamental                         |
+-----+---------------------------------------+
| E46 | Exportación                           |
+-----+---------------------------------------+
| E47 | Pago al Exterior                      |
+-----+---------------------------------------+

Compatibilidad con l10n_do_accounting
--------------------------------------

Si el módulo **l10n_do_accounting** está instalado, el módulo detecta su presencia
automáticamente y archiva los tipos de documento propios para evitar duplicados.
Los registros de l10n_do_accounting (con secuencias fiscales) son los que se usan.

Créditos
--------

* **Autor**: Vicente Tiapa
* **Proveedor e-CF**: `GAE Digital <https://gaedigital.com>`_
* **Normativa**: DGII Norma 06-18 (versión electrónica) · Ley 32-23
* **Licencia**: LGPL-3
