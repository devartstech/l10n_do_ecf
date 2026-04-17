# -*- coding: utf-8 -*-

import logging
import requests

from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

# Mapeo de prefijo de tipo de documento a código ECF
ECF_TYPE_MAP = {
    "E31": "31",  # Factura Crédito Fiscal
    "E32": "32",  # Factura de Consumo
    "E33": "33",  # Nota de Débito
    "E34": "34",  # Nota de Crédito
    "E41": "41",  # Comprobante de Compras
    "E43": "43",  # Gasto Menor
    "E44": "44",  # Régimen Especial de Tributación
    "E45": "45",  # Gubernamental
    "E46": "46",  # Exportación
    "E47": "47",  # Pago al Exterior
}

# ECF types that require sequenceExpDate
ECF_TYPES_WITH_EXP_DATE = {"31", "33", "41", "43", "44", "45"}

# ECF types that require retentionAgentInd per line
ECF_TYPES_WITH_RETENTION = {"41", "47"}

# ECF types that require modifiedNcf (nota de crédito/débito)
ECF_TYPES_WITH_MOD_NCF = {"33", "34"}

# taxTypes mapping by ITBIS percentage
ITBIS_TAX_TYPE_MAP = {
    18: 1,   # ITBIS 18%
    16: 2,   # ITBIS 16%
    0: 3,    # ITBIS 0% exento
}

REQUEST_TIMEOUT = 30  # segundos


class GaeService:
    """
    Servicio de integración con el Gestor de Autorizaciones Electrónicas (GAE)
    de la DGII República Dominicana para la emisión de e-CF.
    """

    def __init__(self, company):
        """
        :param company: recordset de res.company con los campos GAE configurados.
        """
        self.company = company
        self._validate_company_config()

    def _validate_company_config(self):
        """Verifica que la empresa tiene la configuración mínima para conectar al GAE."""
        if not self.company.gae_api_key:
            raise UserError(
                _("La empresa '%s' no tiene configurada la GAE API Key. "
                  "Configure la clave en Ajustes > Facturación Electrónica (eCF).")
                % self.company.name
            )
        if not self.company.gae_api_url:
            raise UserError(
                _("La empresa '%s' no tiene configurada la GAE API URL.")
                % self.company.name
            )

    def _get_headers(self):
        """Construye los headers HTTP necesarios para autenticar con el GAE."""
        return {
            "Content-Type": "application/json",
            "ApiKey": self.company.gae_api_key,
        }

    # ------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------

    def send_invoice(self, invoice):
        """
        Envía la factura/comprobante al GAE de la DGII.

        :param invoice: recordset de account.move.
        :return: dict con claves 'code', 'url', 'date' si la operación fue exitosa.
        :raises UserError: si hay errores de negocio o de comunicación con el GAE.
        """
        payload = self._build_payload(invoice)
        url = "{}/Invoice".format(self.company.gae_api_url.rstrip("/"))

        _logger.info(
            "GAE | Enviando e-CF %s al GAE. URL: %s | Payload: %s",
            invoice.l10n_latam_document_number,
            url,
            payload,
        )

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._get_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise UserError(
                _("Tiempo de espera agotado al conectar con el GAE (timeout %ss). "
                  "Intente nuevamente o contacte al administrador.")
                % REQUEST_TIMEOUT
            )
        except requests.exceptions.ConnectionError as e:
            raise UserError(
                _("No se pudo conectar con el GAE: %s") % str(e)
            )
        except requests.exceptions.RequestException as e:
            raise UserError(
                _("Error inesperado al comunicarse con el GAE: %s") % str(e)
            )

        _logger.info(
            "GAE | Respuesta HTTP %s para e-CF %s: %s",
            response.status_code,
            invoice.l10n_latam_document_number,
            response.text,
        )

        if response.status_code not in (200, 201):
            error_text = self._extract_error_message(response)
            raise UserError(
                _("El GAE rechazó el comprobante %s (HTTP %s): %s")
                % (invoice.l10n_latam_document_number, response.status_code, error_text)
            )

        try:
            data = response.json()
        except ValueError:
            raise UserError(
                _("La respuesta del GAE no es JSON válido: %s") % response.text
            )

        invoice_response = data.get("invoiceResponses", {})
        if not invoice_response:
            raise UserError(
                _("El GAE devolvió una respuesta sin 'invoiceResponses': %s") % data
            )

        return {
            "code": invoice_response.get("code", ""),
            "url": invoice_response.get("url", ""),
            "date": invoice_response.get("date", ""),
        }

    def get_invoice_status(self, rnc, ecf):
        """
        Consulta el estado de un comprobante ya enviado al GAE.

        :param rnc: RNC del emisor.
        :param ecf: Número de comprobante electrónico (ej: E310000050001).
        :return: dict con la respuesta del GAE.
        :raises UserError: en caso de error de comunicación.
        """
        url = "{}/Invoice/GetInvoiceStatus".format(
            self.company.gae_api_url.rstrip("/")
        )
        params = {"rnc": rnc, "ecf": ecf}

        _logger.info("GAE | Consultando estado de e-CF %s (RNC: %s)", ecf, rnc)

        try:
            response = requests.get(
                url,
                params=params,
                headers=self._get_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise UserError(
                _("Tiempo de espera agotado al consultar el estado en el GAE.")
            )
        except requests.exceptions.RequestException as e:
            raise UserError(
                _("Error al consultar estado en el GAE: %s") % str(e)
            )

        if response.status_code != 200:
            raise UserError(
                _("El GAE devolvió HTTP %s al consultar el estado del e-CF %s.")
                % (response.status_code, ecf)
            )

        try:
            return response.json()
        except ValueError:
            raise UserError(
                _("Respuesta inválida del GAE al consultar estado: %s") % response.text
            )

    # ------------------------------------------------------------------
    # Construcción del payload
    # ------------------------------------------------------------------

    def _build_payload(self, invoice):
        """
        Construye el JSON completo que se enviará al endpoint POST /api/Invoice.

        :param invoice: recordset de account.move.
        :return: dict con el payload completo.
        """
        ecf_type = self._get_ecf_type(invoice)
        ecf_number = invoice.l10n_latam_document_number or ""
        payment_condition = self._get_payment_condition(invoice)
        seller_rnc = invoice.company_id.vat or ""
        currency_name = invoice.currency_id.name or "DOP"

        # Tasa de cambio: si la moneda es DOP se envía 1.0
        exchange_rate = 1.0
        if currency_name != "DOP" and invoice.currency_id:
            # Odoo almacena la tasa inversa en currency_id.rate; calculamos
            # cuántos DOP equivalen a 1 unidad de la moneda de la factura.
            try:
                dop = invoice.env.ref("base.DOP")
                rate = invoice.currency_id._get_conversion_rate(
                    invoice.currency_id,
                    dop,
                    invoice.company_id,
                    invoice.invoice_date or invoice.date,
                )
                exchange_rate = round(rate, 4)
            except Exception:
                exchange_rate = 1.0

        payload = {
            "invoiceNumber": invoice.id,
            "ecf": ecf_number,
            "ecfType": ecf_type,
            "sellerRnc": seller_rnc,
            "sellerCode": invoice.company_id.gae_seller_code or "001",
            "creditNoteInd": 1 if ecf_type == "34" else 0,
            "taxedAmountInd": 0,
            "incomeType": "01",
            "paymentCondition": payment_condition,
            "issueDate": invoice.invoice_date.strftime("%Y-%m-%dT00:00:00") if invoice.invoice_date else "",
            "currencyType": currency_name,
            "exchangeRate": exchange_rate,
            "InvoiceTotalAmount": round(invoice.amount_total, 2),
            "TotalTaxedAmount": round(invoice.amount_tax, 2),
            "items": self._build_items(invoice, ecf_type),
        }

        # sequenceExpDate: requerida para tipos E31, E33, E41, E43, E44, E45
        if ecf_type in ECF_TYPES_WITH_EXP_DATE:
            exp_date = self._get_sequence_exp_date(invoice)
            if exp_date:
                payload["sequenceExpDate"] = exp_date

        # paymentDeadline: solo cuando es a crédito
        if payment_condition == "2" and invoice.invoice_date_due:
            payload["paymentDeadline"] = invoice.invoice_date_due.strftime(
                "%Y-%m-%dT00:00:00"
            )

        # Datos del comprador
        buyer_rnc = invoice.partner_id.vat or ""
        buyer_name = invoice.partner_id.name or ""
        buyer_address = self._get_partner_address(invoice.partner_id)
        buyer_phone = invoice.partner_id.phone or invoice.partner_id.mobile or ""

        # E32 con monto < 250,000 DOP: datos del comprador opcionales
        if ecf_type == "32" and invoice.amount_total < 250000:
            if buyer_rnc:
                payload["buyerRnc"] = buyer_rnc
            if buyer_name:
                payload["buyerBusinessName"] = buyer_name
        elif ecf_type == "47":
            # E47: DNI extranjero en lugar de RNC
            foreign_dni = invoice.partner_id.vat or ""
            if foreign_dni:
                payload["foreignDni"] = foreign_dni
            if buyer_name:
                payload["buyerBusinessName"] = buyer_name
        else:
            if buyer_rnc:
                payload["buyerRnc"] = buyer_rnc
            if buyer_name:
                payload["buyerBusinessName"] = buyer_name
            if buyer_address:
                payload["buyerAddress"] = buyer_address
            if buyer_phone:
                payload["buyerPhone"] = buyer_phone

        # Campos para nota de crédito / nota de débito
        if ecf_type in ECF_TYPES_WITH_MOD_NCF:
            original_invoice = self._get_original_invoice(invoice)
            if original_invoice:
                payload["modifiedNcf"] = original_invoice.l10n_latam_document_number or ""
                if invoice.invoice_date:
                    payload["modifDateNcf"] = invoice.invoice_date.strftime(
                        "%Y-%m-%dT00:00:00"
                    )
            # Código y descripción de razón de modificación
            mod_code = invoice.l10n_do_ecf_modification_code
            if mod_code:
                try:
                    payload["modifReasonId"] = int(mod_code)
                except (ValueError, TypeError):
                    payload["modifReasonId"] = 1
                payload["modifReasonDesc"] = dict(
                    invoice._fields["l10n_do_ecf_modification_code"].selection
                ).get(mod_code, "")

        return payload

    def _build_items(self, invoice, ecf_type):
        """
        Construye la lista de items a partir de las líneas de factura de Odoo.

        :param invoice: recordset de account.move.
        :param ecf_type: str con el código ECF (ej: "31").
        :return: list de dicts con los items.
        """
        items = []
        line_number = 1

        # Solo líneas de producto (excluir secciones, notas y líneas de impuesto)
        product_lines = invoice.invoice_line_ids.filtered(
            lambda l: l.display_type not in ("line_section", "line_note")
        )

        for line in product_lines:
            tax_type = self._get_itbis_type(line, ecf_type)
            unit_price = round(line.price_unit, 4)
            quantity = round(line.quantity, 4)
            item_amount = round(line.price_subtotal, 2)
            discount_amount = round(
                (line.price_unit * line.quantity) - line.price_subtotal, 2
            ) if line.discount else 0.0

            item = {
                "lineNumber": line_number,
                "itemDescription": line.name or line.product_id.name or "Producto",
                "serviceInd": self._get_service_indicator(line),
                "itemQuantity": quantity,
                "unitMeasure": "43",  # Unidad por defecto
                "unitPrice": unit_price,
                "itemAmount": item_amount,
                "taxTypes": tax_type,
            }

            if discount_amount > 0:
                item["discountAmount"] = discount_amount

            # Retenciones: solo para E41 y E47
            if ecf_type in ECF_TYPES_WITH_RETENTION:
                item["retentionAgentInd"] = 1
                item["itbisRetAmount"] = round(
                    self._compute_itbis_retention(line), 2
                )
                item["isrRetAmount"] = round(
                    self._compute_isr_retention(line), 2
                )

            items.append(item)
            line_number += 1

        return items

    # ------------------------------------------------------------------
    # Métodos de ayuda / mapeo
    # ------------------------------------------------------------------

    def _get_ecf_type(self, invoice):
        """
        Determina el código de tipo ECF ("31", "32", etc.) a partir del
        tipo de documento fiscal de la factura.

        :param invoice: recordset de account.move.
        :return: str con el código ECF de dos dígitos.
        :raises UserError: si el tipo de documento no es un e-CF conocido.
        """
        doc_type = invoice.l10n_latam_document_type_id
        if not doc_type:
            raise UserError(
                _("La factura %s no tiene tipo de documento fiscal asignado.")
                % (invoice.name or invoice.id)
            )

        prefix = (doc_type.doc_code_prefix or "").upper().strip()
        ecf_code = ECF_TYPE_MAP.get(prefix)
        if not ecf_code:
            raise UserError(
                _("El tipo de documento '%s' (prefijo: %s) no corresponde a un "
                  "Comprobante Fiscal Electrónico (e-CF) soportado.")
                % (doc_type.name, prefix)
            )
        return ecf_code

    def _get_itbis_type(self, line, ecf_type=None):
        """
        Mapea los impuestos de una línea de factura al código taxTypes del GAE.

        Lógica:
        - E46 (Exportación) → siempre taxTypes=3
        - E43 (Gasto Menor) → siempre taxTypes=4
        - Línea sin impuestos → taxTypes=0
        - ITBIS 18% → taxTypes=1
        - ITBIS 16% → taxTypes=2
        - ITBIS 0% / Exento con tasa 0 → taxTypes=3
        - Exento / No gravado → taxTypes=4

        :param line: recordset de account.move.line.
        :param ecf_type: str código ECF (opcional, para reglas especiales).
        :return: int taxTypes.
        """
        if ecf_type == "46":
            return 3
        if ecf_type == "43":
            return 4

        if not line.tax_ids:
            return 0

        for tax in line.tax_ids:
            amount = abs(tax.amount)
            if tax.amount_type == "percent":
                if amount == 18:
                    return 1
                elif amount == 16:
                    return 2
                elif amount == 0:
                    # Distinguir entre exento 0% (taxTypes=3) y exento sin base (taxTypes=4)
                    tax_name_lower = (tax.name or "").lower()
                    if "0%" in tax_name_lower or "itbis 0" in tax_name_lower:
                        return 3
                    return 4
            # Impuesto de monto fijo u otro tipo
            if amount == 0:
                return 4

        # Si tiene impuestos pero ninguno es ITBIS reconocido, marcar exento
        return 4

    def _get_payment_condition(self, invoice):
        """
        Determina si la factura es a contado ("1") o a crédito ("2").

        :param invoice: recordset de account.move.
        :return: str "1" o "2".
        """
        if invoice.invoice_payment_term_id:
            # "Immediate Payment" o términos sin días → contado
            lines = invoice.invoice_payment_term_id.line_ids
            has_days = any(
                (line.nb_days or 0) > 0 for line in lines
            )
            return "2" if has_days else "1"
        # Sin término de pago: si la fecha de vencimiento es igual a la de emisión → contado
        if invoice.invoice_date and invoice.invoice_date_due:
            return "2" if invoice.invoice_date_due > invoice.invoice_date else "1"
        return "1"

    def _get_sequence_exp_date(self, invoice):
        """
        Obtiene la fecha de vencimiento de la secuencia fiscal del comprobante.
        Viene de account.fiscal.sequence vinculada a la factura.

        :param invoice: recordset de account.move.
        :return: str con fecha en formato ISO o None.
        """
        fiscal_seq = getattr(invoice, "l10n_do_fiscal_sequence_id", None)
        if fiscal_seq and fiscal_seq.expiration_date:
            return fiscal_seq.expiration_date.strftime("%Y-%m-%dT00:00:00")
        return None

    def _get_partner_address(self, partner):
        """
        Construye una cadena de dirección legible para el comprador.

        :param partner: recordset de res.partner.
        :return: str con la dirección.
        """
        parts = filter(None, [
            partner.street,
            partner.street2,
            partner.city,
            partner.state_id.name if partner.state_id else "",
            partner.country_id.name if partner.country_id else "",
        ])
        return ", ".join(parts)

    def _get_original_invoice(self, invoice):
        """
        Obtiene la factura original referenciada por una nota de crédito o débito.

        :param invoice: recordset de account.move.
        :return: recordset de account.move o None.
        """
        # Nota de crédito: Odoo vincula la original en reversed_entry_id
        if hasattr(invoice, "reversed_entry_id") and invoice.reversed_entry_id:
            return invoice.reversed_entry_id
        # Nota de débito: puede estar en debit_origin_id (módulo account_debit_note)
        if hasattr(invoice, "debit_origin_id") and invoice.debit_origin_id:
            return invoice.debit_origin_id
        return None

    def _get_service_indicator(self, line):
        """
        Determina si la línea corresponde a un servicio (1) o a un bien (2).

        :param line: recordset de account.move.line.
        :return: int 1 (servicio) o 2 (bien).
        """
        product = line.product_id
        if not product:
            return 1  # Sin producto → servicio por defecto
        if product.type == "service":
            return 1
        return 2

    def _compute_itbis_retention(self, line):
        """
        Calcula el monto de retención de ITBIS para una línea (E41/E47).
        Se aplica el 100% del ITBIS calculado como retención.

        :param line: recordset de account.move.line.
        :return: float monto de retención ITBIS.
        """
        itbis_amount = 0.0
        for tax in line.tax_ids.filtered(
            lambda t: t.amount_type == "percent" and abs(t.amount) in (16, 18)
        ):
            itbis_amount += line.price_subtotal * (abs(tax.amount) / 100)
        return itbis_amount

    def _compute_isr_retention(self, line):
        """
        Calcula el monto de retención ISR para una línea (E41/E47).
        Busca impuestos de tipo ISR en la línea.

        :param line: recordset de account.move.line.
        :return: float monto de retención ISR.
        """
        isr_amount = 0.0
        for tax in line.tax_ids.filtered(
            lambda t: t.amount_type == "percent"
            and any(
                kw in (t.name or "").lower()
                for kw in ("isr", "renta", "retención isr", "ret. isr")
            )
        ):
            isr_amount += line.price_subtotal * (abs(tax.amount) / 100)
        return isr_amount

    @staticmethod
    def _extract_error_message(response):
        """
        Intenta extraer un mensaje de error legible de la respuesta HTTP.

        :param response: objeto requests.Response.
        :return: str con el mensaje de error.
        """
        try:
            data = response.json()
            # Intentar distintas claves comunes
            for key in ("message", "Message", "error", "Error", "detail", "Detail"):
                if key in data:
                    return str(data[key])
            return str(data)
        except ValueError:
            return response.text or "Error desconocido"
