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
