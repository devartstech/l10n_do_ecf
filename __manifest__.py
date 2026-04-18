# -*- coding: utf-8 -*-

{
    "name": "Facturación Electrónica (eCF) - GAE",
    "summary": """
        Integración con el Gestor de Autorizaciones Electrónicas (GAE) de la DGII
        para la emisión de Comprobantes Fiscales Electrónicos (e-CF) en
        República Dominicana.""",
    "author": "Vicente Tiapa",
    "category": "Localization/Dominican Republic",
    "license": "LGPL-3",
    "version": "17.0.1.0.0",
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
    "installable": True,
    "application": False,
    "auto_install": False,
}
