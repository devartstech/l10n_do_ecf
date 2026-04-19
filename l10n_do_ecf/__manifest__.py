# -*- coding: utf-8 -*-

{
    "name": "Facturación Electrónica (e-CF) — GAE · República Dominicana",
    "summary": "Emisión de e-CF vía GAE Digital (DGII) — E31 a E47, timbre digital, QR en PDF.",
    "description": "Integración nativa con el Gestor de Autorizaciones Electrónicas (GAE) de la DGII "
                   "para la emisión de Comprobantes Fiscales Electrónicos bajo la Ley 32-23.",
    "author": "Vicente Tiapa",
    "website": "https://gaedigital.com/facturacionelectronica/",
    "category": "Accounting/Localizations/Account Charts",
    "license": "LGPL-3",
    "version": "17.0.1.2.0",
    "support": "vicentetiapa95@gmail.com",
    "depends": ["account", "l10n_latam_invoice_document"],
    "data": [
        "security/ir.model.access.csv",
        "data/ecf_type_data.xml",
        "data/l10n_latam_document_type_data.xml",
        "views/res_config_settings_view.xml",
        "views/account_move_views.xml",
        "views/account_tax_views.xml",
        "views/report_invoice_ecf.xml",
    ],
    "external_dependencies": {
        "python": ["qrcode", "requests"],
    },
    "post_init_hook": "post_init_hook",
    "post_migrate": "post_migrate",
    "images": ["static/description/screenshot.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
