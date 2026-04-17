# -*- coding: utf-8 -*-

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    gae_api_key = fields.Char(
        string="GAE API Key",
        help="Clave de autenticación para el Gestor de Autorizaciones Electrónicas "
             "(GAE) de la DGII. Se enviará como header 'ApiKey' en formato base64.",
    )
    gae_api_url = fields.Char(
        string="GAE API URL",
        default="https://ecf.dgii.gov.do/api",
        help="URL base del servicio GAE de la DGII. "
             "No incluir barra final.",
    )
    gae_environment = fields.Selection(
        selection=[
            ("test", "Test"),
            ("production", "Producción"),
        ],
        string="Entorno GAE",
        default="test",
        help="Entorno de conexión al GAE. En modo Test se usa el ambiente de "
             "certificación de la DGII.",
    )
    gae_seller_code = fields.Char(
        string="Código Punto de Venta",
        default="001",
        help="Código del punto de venta (sellerCode) requerido por el GAE. "
             "Por defecto '001'.",
    )
    gae_income_type = fields.Selection(
        selection=[
            ("01", "01 - Ingresos por operaciones (No financieros)"),
            ("02", "02 - Ingresos Financieros"),
            ("03", "03 - Ingresos Extraordinarios"),
            ("04", "04 - Ingresos por Arrendamientos"),
            ("05", "05 - Ingresos por Venta de Activo Depreciable"),
            ("06", "06 - Otros Ingresos"),
        ],
        string="Tipo de Ingreso (eCF)",
        default="01",
        help="Tipo de ingreso por defecto para los comprobantes electrónicos (incomeType).",
    )
