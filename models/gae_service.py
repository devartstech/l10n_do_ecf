# -*- coding: utf-8 -*-

import logging
import time
from datetime import datetime

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

# ECF types that require sequenceExpDate (todos excepto E32 y E34)
ECF_TYPES_WITH_EXP_DATE = {"31", "33", "41", "43", "44", "45", "46", "47"}

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

REQUEST_TIMEOUT = 60  # segundos
_GAE_MAX_RETRIES = 3
_GAE_RETRY_BACKOFF = 2  # segundos base; intento N espera N*backoff


class GaeNetworkError(UserError):
    """
    Error de comunicación con el GAE (timeout, conexión rechazada, etc.).
    Distinto de un rechazo DGII — indica que el e-CF puede aún no haber llegado.
    """
    pass


class GaeService:
    """
    Servicio de integración con el Gestor de Autorizaciones Electrónicas (GAE)
    de la DGII República Dominicana para la emisión de e-CF.
    """

    def __init__(self, company):
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

    def _make_request(self, method, url, **kwargs):
        """
        Realiza una petición HTTP con reintentos automáticos ante errores de red.
        Reintenta hasta _GAE_MAX_RETRIES veces con backoff en Timeout y ConnectionError.
        """
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        last_exc = None

        for attempt in range(1, _GAE_MAX_RETRIES + 1):
            _logger.debug(
                "GAE | %s %s (intento %d/%d)", method.upper(), url, attempt, _GAE_MAX_RETRIES
            )
            t_start = datetime.now()
            try:
                response = requests.request(method, url, **kwargs)
                elapsed = (datetime.now() - t_start).total_seconds()
                _logger.debug(
                    "GAE | HTTP %s en %.2fs — %s %s",
                    response.status_code, elapsed, method.upper(), url,
                )
                return response

            except requests.exceptions.Timeout as exc:
                last_exc = exc
                _logger.warning(
                    "GAE | Timeout en intento %d/%d — URL: %s", attempt, _GAE_MAX_RETRIES, url
                )
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                _logger.warning(
                    "GAE | Error de conexión en intento %d/%d — %s",
                    attempt, _GAE_MAX_RETRIES, str(exc)[:120],
                )
            except requests.exceptions.RequestException as exc:
                raise GaeNetworkError(
                    _("Error inesperado al comunicarse con el GAE: %s") % str(exc)
                )

            if attempt < _GAE_MAX_RETRIES:
                wait = attempt * _GAE_RETRY_BACKOFF
                _logger.info("GAE | Reintentando en %ds…", wait)
                time.sleep(wait)

        raise GaeNetworkError(
            _("No se pudo conectar con el GAE después de %d intentos (timeout %ss). "
              "Verifique la conexión e intente nuevamente más tarde.")
            % (_GAE_MAX_RETRIES, REQUEST_TIMEOUT)
        )

    # ------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------

    def validate_pre_flight(self, invoice):
        """
        Valida los datos mínimos del comprobante antes de construir el payload.
        Lanza UserError con lista de problemas encontrados.
        """
        errors = []

        if not invoice.company_id.vat:
            errors.append(
                _("La empresa '%s' no tiene RNC/NIT configurado.") % invoice.company_id.name
            )

        if not invoice.invoice_date:
            errors.append(_("La factura no tiene fecha de emisión asignada."))

        product_lines = invoice.invoice_line_ids.filtered(
            lambda l: l.display_type not in ("line_section", "line_note")
        )
        if not product_lines:
            errors.append(_("La factura no tiene líneas de artículos o servicios."))

        ecf_type = None
        try:
            ecf_type = self._get_ecf_type(invoice)
        except UserError as exc:
            errors.append(str(exc.args[0]) if exc.args else str(exc))

        if ecf_type and ecf_type in ECF_TYPES_WITH_MOD_NCF:
            mod_code = getattr(invoice, "l10n_do_ecf_modification_code", None)
            if not mod_code:
                errors.append(
                    _("Las notas de crédito/débito electrónicas requieren seleccionar "
                      "la Razón de Modificación en la pestaña 'Información e-CF'.")
                )
            if not self._get_original_invoice(invoice):
                errors.append(
                    _("No se encontró la factura original referenciada por esta nota de crédito/débito.")
                )

        if errors:
            raise UserError(
                _("No se puede enviar al GAE. Corrija los siguientes problemas:\n\n%s")
                % "\n".join("• " + e for e in errors)
            )

    def send_invoice(self, invoice):
        """
        Envía la factura/comprobante al GAE de la DGII.

        :param invoice: recordset de account.move.
        :return: dict con claves 'code', 'url', 'date' si la operación fue exitosa.
        :raises GaeNetworkError: si hay problemas de conectividad.
        :raises UserError: si el GAE rechaza el comprobante o hay error de validación.
        """
        payload = self._build_payload(invoice)
        url = "{}/Invoice".format(self.company.gae_api_url.rstrip("/"))

        _logger.info(
            "GAE | Enviando e-CF %s (id=%s) — tipo: %s, empresa: %s",
            invoice.l10n_latam_document_number,
            invoice.id,
            payload.get("ecfType"),
            invoice.company_id.name,
        )
        _logger.debug("GAE | Payload e-CF %s: %s", invoice.l10n_latam_document_number, payload)

        response = self._make_request("post", url, json=payload, headers=self._get_headers())

        _logger.info(
            "GAE | Respuesta HTTP %s para e-CF %s",
            response.status_code,
            invoice.l10n_latam_document_number,
        )
        _logger.debug(
            "GAE | Cuerpo respuesta e-CF %s: %s",
            invoice.l10n_latam_document_number,
            response.text[:2000],
        )

        if response.status_code not in (200, 201):
            error_text = self._extract_error_message(response)
            raise UserError(
                _("El GAE rechazó el comprobante %s (HTTP %s):\n%s")
                % (invoice.l10n_latam_document_number, response.status_code, error_text)
            )

        try:
            data = response.json()
        except ValueError:
            raise UserError(
                _("La respuesta del GAE no es JSON válido para el e-CF %s.")
                % invoice.l10n_latam_document_number
            )

        invoice_response = data.get("invoiceResponses", {})
        if not invoice_response:
            _logger.warning(
                "GAE | Respuesta sin 'invoiceResponses' para e-CF %s: %s",
                invoice.l10n_latam_document_number,
                str(data)[:500],
            )
            raise UserError(
                _("El GAE devolvió una respuesta inesperada para el e-CF %s. "
                  "Consulte los logs del servidor para más detalles.")
                % invoice.l10n_latam_document_number
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
        :raises GaeNetworkError: en caso de error de comunicación.
        :raises UserError: en caso de respuesta inválida.
        """
        url = "{}/Invoice/GetInvoiceStatus".format(self.company.gae_api_url.rstrip("/"))
        params = {"rnc": rnc, "ecf": ecf}

        _logger.info("GAE | Consultando estado de e-CF %s (RNC: %s)", ecf, rnc)

        response = self._make_request("get", url, params=params, headers=self._get_headers())

        if response.status_code != 200:
            error_text = self._extract_error_message(response)
            raise UserError(
                _("El GAE devolvió HTTP %s al consultar el estado del e-CF %s:\n%s")
                % (response.status_code, ecf, error_text)
            )

        try:
            data = response.json()
            _logger.debug("GAE | Estado e-CF %s: %s", ecf, data)
            return data
        except ValueError:
            raise UserError(
                _("Respuesta inválida del GAE al consultar estado del e-CF %s.") % ecf
            )

    def get_invoice_info(self, rnc, ecf):
        """
        Obtiene el timbre digital (código de seguridad, URL y fecha de firma)
        de un e-CF ya aprobado por el GAE.

        Endpoint: GET /api/Invoice/GetInvoiceInfo
        Retorna invoiceResponses { date, code, url }.
        """
        url = "{}/Invoice/GetInvoiceInfo".format(self.company.gae_api_url.rstrip("/"))
        params = {"rnc": rnc, "ecf": ecf}

        _logger.info("GAE | Consultando timbre de e-CF %s (RNC: %s)", ecf, rnc)

        response = self._make_request("get", url, params=params, headers=self._get_headers())

        if response.status_code != 200:
            error_text = self._extract_error_message(response)
            raise UserError(
                _("El GAE devolvió HTTP %s al consultar timbre del e-CF %s:\n%s")
                % (response.status_code, ecf, error_text)
            )

        try:
            data = response.json()
        except ValueError:
            raise UserError(
                _("Respuesta inválida del GAE al consultar timbre del e-CF %s.") % ecf
            )

        invoice_responses = data.get("invoiceResponses") or {}
        return {
            "code": invoice_responses.get("code", ""),
            "url": invoice_responses.get("url", ""),
            "date": invoice_responses.get("date", ""),
        }

    # ------------------------------------------------------------------
    # Construcción del payload
    # ------------------------------------------------------------------

    def _compute_credit_note_ind(self, invoice, ecf_type):
        """
        creditNoteInd según DGII:
        0 = factura original emitida hace ≤ 30 días (tiene derecho a rebajar ITBIS)
        1 = factura original emitida hace > 30 días (no tiene derecho a rebajar ITBIS)
        Solo aplica para E34. Para otros tipos siempre 0.
        """
        if ecf_type != "34":
            return 0
        original = self._get_original_invoice(invoice)
        if not original or not original.invoice_date:
            return 0
        from datetime import date
        delta = (invoice.invoice_date or date.today()) - original.invoice_date
        return 1 if delta.days > 30 else 0

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
                _logger.warning(
                    "GAE | No se pudo calcular tasa %s→DOP para e-CF %s, usando 1.0",
                    currency_name, ecf_number,
                )
                exchange_rate = 1.0

        # E46 y E47: taxedAmountInd debe ser null
        taxed_amount_ind = None if ecf_type in ("46", "47") else 0

        # TotalTaxedAmount = ITBIS + impuestos adicionales (ISC, propina, telecom)
        total_itbis = self._compute_total_itbis(invoice)
        total_additional = self._compute_total_additional_taxes(invoice, ecf_type)

        # InvoiceTotalAmount: para E41/E47 restamos retenciones al total
        total_retenciones = self._compute_total_retenciones(invoice, ecf_type)
        invoice_total = round(invoice.amount_untaxed + total_itbis + total_additional - total_retenciones, 2)

        # E43 (Gasto Menor): TotalTaxedAmount = monto total
        if ecf_type == "43":
            total_taxed = invoice_total
        else:
            total_taxed = round(total_itbis + total_additional, 2)

        # sequenceExpDate debe ir ANTES de creditNoteInd e incomeType en el XML DGII
        seq_exp_date = None
        if ecf_type in ECF_TYPES_WITH_EXP_DATE:
            seq_exp_date = self._get_sequence_exp_date(invoice)
            if not seq_exp_date:
                raise UserError(
                    _("El comprobante %s (tipo E%s) requiere una fecha de vencimiento de "
                      "secuencia (FechaVencimientoSecuencia). Configure la secuencia fiscal "
                      "e-CF para este tipo de comprobante.")
                    % (invoice.l10n_latam_document_number, ecf_type)
                )

        payload = {
            "invoiceNumber": invoice.id,
            "ecf": ecf_number,
            "ecfType": ecf_type,
            "sellerRnc": seller_rnc,
            "sellerCode": invoice.company_id.gae_seller_code or "001",
            "sequenceExpDate": seq_exp_date,
            "creditNoteInd": self._compute_credit_note_ind(invoice, ecf_type),
            "taxedAmountInd": taxed_amount_ind,
            "incomeType": invoice.company_id.gae_income_type or "01",
            "paymentCondition": payment_condition,
            "issueDate": invoice.invoice_date.strftime("%Y-%m-%dT00:00:00") if invoice.invoice_date else "",
            "currencyType": currency_name,
            "exchangeRate": exchange_rate,
            "InvoiceTotalAmount": invoice_total,
            "TotalTaxedAmount": total_taxed,
            "items": self._build_items(invoice, ecf_type),
        }

        # paymentDeadline: solo cuando es a crédito — formato DD-MM-AAAA según DGII
        if payment_condition == "2" and invoice.invoice_date_due:
            payload["paymentDeadline"] = invoice.invoice_date_due.strftime("%d-%m-%Y")

        # Datos del comprador
        buyer_rnc = invoice.partner_id.vat or ""
        buyer_name = invoice.partner_id.name or ""
        buyer_address = self._get_partner_address(invoice.partner_id)
        raw_phone = invoice.partner_id.phone or invoice.partner_id.mobile or ""
        digits = "".join(filter(str.isdigit, raw_phone))
        if digits.startswith("1") and len(digits) == 11:
            digits = digits[1:]
        buyer_phone = "{}-{}-{}".format(digits[:3], digits[3:6], digits[6:]) if len(digits) == 10 else raw_phone

        # E43 (Gasto Menor) y E47 (Pago Exterior): sin datos de comprador
        if ecf_type == "43":
            pass
        elif ecf_type == "47":
            foreign_dni = invoice.partner_id.vat or ""
            if foreign_dni:
                payload["foreignDni"] = foreign_dni
        # E32 con monto < 250,000 DOP: datos del comprador opcionales
        elif ecf_type == "32" and invoice.amount_total < 250000:
            if buyer_rnc:
                payload["buyerRnc"] = buyer_rnc
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
                payload["rncNcfModified"] = original_invoice.company_id.vat or seller_rnc
                orig_date = original_invoice.invoice_date or original_invoice.date
                if orig_date:
                    payload["modifDateNcf"] = orig_date.strftime("%d-%m-%Y")
            mod_code = getattr(invoice, "l10n_do_ecf_modification_code", None)
            if mod_code:
                try:
                    payload["modifReasonId"] = int(mod_code)
                except (ValueError, TypeError):
                    payload["modifReasonId"] = 1
                field = invoice._fields.get("l10n_do_ecf_modification_code")
                if field and hasattr(field, "selection"):
                    payload["modifReasonDesc"] = dict(field.selection).get(mod_code, "")

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

        product_lines = invoice.invoice_line_ids.filtered(
            lambda l: l.display_type not in ("line_section", "line_note")
        )

        for line in product_lines:
            tax_type = self._get_itbis_type(line, ecf_type)
            quantity = round(line.quantity, 4)
            item_amount = round(line.price_subtotal, 2)
            discount_amount = round(
                line.price_unit * line.quantity - line.price_subtotal, 2
            ) if line.discount else 0.0

            item = {
                "lineNumber": line_number,
                "itemDescription": line.name or (line.product_id.name if line.product_id else "Producto"),
                "serviceInd": self._get_service_indicator(line),
                "itemQuantity": quantity,
                "unitMeasure": self._get_unit_measure(line),
                "unitPrice": round(line.price_unit, 4),
                "itemAmount": item_amount,
                "taxTypes": tax_type,
            }

            if discount_amount > 0:
                item["discountAmount"] = discount_amount

            # Impuestos adicionales (ISC, propina, telecom) — no aplica E41/E43/E47
            if ecf_type not in ("41", "43", "47"):
                additional = self._get_additional_taxes(line)
                if additional:
                    item["aditionalTaxes"] = additional

            # Retenciones: solo para E41 y E47
            if ecf_type in ECF_TYPES_WITH_RETENTION:
                item["retentionAgentInd"] = 1
                item["itbisRetAmount"] = round(self._compute_itbis_retention(line), 2)
                item["isrRetAmount"] = round(self._compute_isr_retention(line), 2)

            items.append(item)
            line_number += 1

        return items

    # ------------------------------------------------------------------
    # Métodos de ayuda / mapeo
    # ------------------------------------------------------------------

    def _get_ecf_type(self, invoice):
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
        if ecf_type == "46":
            return 3
        if ecf_type in ("43", "47"):
            return 4
        if ecf_type == "44":
            if line.tax_ids:
                for tax in line.tax_ids:
                    if tax.amount_type == "percent" and tax.amount == 0:
                        name_lower = (tax.name or "").lower()
                        if "0%" in name_lower or "itbis 0" in name_lower:
                            return 3
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
                    tax_name_lower = (tax.name or "").lower()
                    if "0%" in tax_name_lower or "itbis 0" in tax_name_lower:
                        return 3
                    return 4
            if amount == 0:
                return 4
        return 4

    def _get_payment_condition(self, invoice):
        if invoice.invoice_payment_term_id:
            lines = invoice.invoice_payment_term_id.line_ids
            has_days = any((line.nb_days or 0) > 0 for line in lines)
            return "2" if has_days else "1"
        if invoice.invoice_date and invoice.invoice_date_due:
            return "2" if invoice.invoice_date_due > invoice.invoice_date else "1"
        return "1"

    def _get_sequence_exp_date(self, invoice):
        fiscal_seq = getattr(invoice, "l10n_do_fiscal_sequence_id", None)
        if fiscal_seq and getattr(fiscal_seq, "expiration_date", None):
            return fiscal_seq.expiration_date.strftime("%Y-%m-%d")
        exp_date = getattr(invoice, "l10n_do_ecf_sequence_exp_date", None)
        if exp_date:
            return exp_date.strftime("%Y-%m-%d")
        return None

    def _get_partner_address(self, partner):
        parts = filter(None, [
            partner.street,
            partner.street2,
            partner.city,
            partner.state_id.name if partner.state_id else "",
            partner.country_id.name if partner.country_id else "",
        ])
        return ", ".join(parts)

    def _get_original_invoice(self, invoice):
        if hasattr(invoice, "reversed_entry_id") and invoice.reversed_entry_id:
            return invoice.reversed_entry_id
        if hasattr(invoice, "debit_origin_id") and invoice.debit_origin_id:
            return invoice.debit_origin_id
        return None

    def _get_service_indicator(self, line):
        product = line.product_id
        if not product:
            return "2"
        if product.type == "service":
            return "2"
        return "1"

    def _get_unit_measure(self, line):
        UOM_MAP = {
            "unidad": "43", "unit": "43", "units": "43", "und": "43",
            "kg": "21", "kilogram": "21", "kilogramo": "21",
            "g": "17", "gram": "17", "gramo": "17",
            "l": "24", "liter": "24", "litro": "24",
            "m": "26", "meter": "26", "metro": "26",
            "lb": "23", "pound": "23", "libra": "23",
            "caja": "6", "box": "6",
            "dozen": "13", "docena": "13",
            "hour": "43", "hora": "43",
        }
        if line.product_uom_id:
            uom_name = (line.product_uom_id.name or "").lower().strip()
            for key, code in UOM_MAP.items():
                if key in uom_name:
                    return code
        return "43"

    def _compute_total_itbis(self, invoice):
        total = 0.0
        for line in invoice.line_ids.filtered(lambda l: l.tax_line_id):
            tax = line.tax_line_id
            if tax.amount_type == "percent" and tax.amount > 0 and abs(tax.amount) in (16, 18):
                total += abs(line.balance)
        return total

    def _compute_total_retenciones(self, invoice, ecf_type):
        if ecf_type not in ECF_TYPES_WITH_RETENTION:
            return 0.0
        total = 0.0
        for line in invoice.line_ids.filtered(lambda l: l.tax_line_id):
            tax = line.tax_line_id
            if tax.amount_type == "percent" and tax.amount < 0:
                total += abs(line.balance)
        return total

    def _compute_itbis_retention(self, line):
        itbis_amount = 0.0
        for tax in line.tax_ids.filtered(
            lambda t: t.amount_type == "percent" and abs(t.amount) in (16, 18)
        ):
            itbis_amount += line.price_subtotal * (abs(tax.amount) / 100)
        return itbis_amount

    def _compute_isr_retention(self, line):
        isr_amount = 0.0
        for tax in line.tax_ids.filtered(
            lambda t: t.amount_type == "percent"
            and any(kw in (t.name or "").lower() for kw in ("isr", "renta", "retención isr", "ret. isr"))
        ):
            isr_amount += line.price_subtotal * (abs(tax.amount) / 100)
        return isr_amount

    def _get_additional_taxes(self, line):
        additional = []
        for tax in line.tax_ids:
            gae_type = getattr(tax, "l10n_do_gae_additional_tax_type", None)
            if not gae_type:
                continue
            if tax.amount_type == "percent" and abs(tax.amount) in (16, 18):
                continue
            if tax.amount < 0:
                continue
            rate = abs(tax.amount)
            amount = round(line.price_subtotal * (rate / 100), 2)
            additional.append({"type": gae_type, "rate": rate, "amount": amount})
        return additional if additional else None

    def _compute_total_additional_taxes(self, invoice, ecf_type):
        if ecf_type in ("41", "43", "47"):
            return 0.0
        total = 0.0
        for line in invoice.invoice_line_ids.filtered(
            lambda l: l.display_type not in ("line_section", "line_note")
        ):
            for tax in line.tax_ids:
                gae_type = getattr(tax, "l10n_do_gae_additional_tax_type", None)
                if not gae_type:
                    continue
                if tax.amount_type == "percent" and abs(tax.amount) in (16, 18):
                    continue
                if tax.amount < 0:
                    continue
                total += line.price_subtotal * (abs(tax.amount) / 100)
        return round(total, 2)

    @staticmethod
    def _extract_error_message(response):
        """
        Extrae un mensaje de error legible de la respuesta HTTP del GAE.
        Maneja estructuras planas, arrays de errores y objetos anidados.
        """
        try:
            data = response.json()

            # Extraer mensaje principal
            main_msg = ""
            for key in ("message", "Message", "error", "Error", "detail", "Detail", "title", "Title"):
                if key in data and data[key]:
                    main_msg = str(data[key])
                    break

            # Extraer detalles adicionales desde arrays de errores
            detail_lines = []
            for detail_key in ("details", "Details", "errors", "Errors", "validationErrors"):
                raw = data.get(detail_key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else [raw]
                for item in items[:15]:
                    if isinstance(item, dict):
                        code = item.get("code") or item.get("Code") or item.get("errorCode") or ""
                        desc = (
                            item.get("description") or item.get("Description")
                            or item.get("message") or item.get("Message")
                            or str(item)
                        )
                        detail_lines.append("[{}] {}".format(code, desc) if code else str(desc))
                    elif item:
                        detail_lines.append(str(item))
                break

            if main_msg and detail_lines:
                return "{}\n• {}".format(main_msg, "\n• ".join(detail_lines))
            if main_msg:
                return main_msg
            if detail_lines:
                return "• " + "\n• ".join(detail_lines)

            # Respuesta en forma de lista directa
            if isinstance(data, list):
                parts = []
                for item in data[:15]:
                    if isinstance(item, dict):
                        desc = item.get("description") or item.get("message") or str(item)
                        code = item.get("code") or item.get("Code") or ""
                        parts.append("[{}] {}".format(code, desc) if code else str(desc))
                    else:
                        parts.append(str(item))
                return "\n".join(parts) if parts else str(data)[:500]

            return str(data)[:500]

        except ValueError:
            return (response.text or "Error desconocido")[:500]
