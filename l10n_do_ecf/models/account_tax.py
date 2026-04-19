# -*- coding: utf-8 -*-

from odoo import fields, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    l10n_do_gae_additional_tax_type = fields.Selection(
        selection=[
            ("001", "001 - ISC Específico (por unidad)"),
            ("002", "002 - ISC Ad Valorem / Telecom"),
            ("003", "003 - Propina Legal (10%)"),
            ("004", "004 - ISC Bebidas Alcohólicas"),
            ("005", "005 - ISC Productos de Tabaco"),
            ("006", "006 - Otros ISC Específicos"),
        ],
        string="Tipo Impuesto Adicional GAE",
        help="Código de impuesto adicional (Tabla I DGII) para incluir en el array "
             "'aditionalTaxes' del payload GAE. Solo configurar en impuestos que NO sean "
             "ITBIS ni retenciones (p.ej. ISC, propina legal, telecom).",
    )
