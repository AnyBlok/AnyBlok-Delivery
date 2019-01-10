"""Colissimo Carrier Classes
"""
import json
from datetime import datetime
from logging import getLogger

import requests
from requests_toolbelt.multipart import decoder
from anyblok import Declarations
from lxml import etree
from .eventcodes import eventCodes


logger = getLogger(__name__)
Model = Declarations.Model


@Declarations.register(Model.Delivery.Carrier)
class Service:

    @classmethod
    def get_carriers(cls):
        res = super(Service, cls).get_carriers()
        res.update(dict(COLISSIMO='Colissimo'))
        return res


@Declarations.register(
    Model.Delivery.Carrier.Service,
    tablename=Model.Delivery.Carrier.Service)
class Colissimo(Model.Delivery.Carrier.Service):
    """ The Colissimo Carrier service (Polymorphic model that's override
    Model.Delivery.Carrier.service)

    Namespace : Model.Delivery.Carrier.Service.Colissimo
    """
    CARRIER_CODE = "COLISSIMO"

    def map_data(self, shipment=None):
        """Given a shipment object, transform its data to Colissimo
        specifications"""
        if not shipment:
            raise Exception("You must pass a shipment object to map_data")

        sh = shipment
        # datetime formatting
        deposit_date = datetime.now().strftime("%Y-%m-%d")
        # 2 letters country code
        sender_country = sh.sender_address.country.alpha_2
        recipient_country = sh.recipient_address.country.alpha_2

        data = {"contractNumber": "%s" % self.credential.account_number,
                "password": "%s" % self.credential.password,
                "outputFormat": {
                    "x": "0",
                    "y": "0",
                    "outputPrintingType": "PDF_A4_300dpi"
                    },
                "letter": {
                    "service": {
                        "productCode": "%s" % self.product_code,
                        "depositDate": "%s" % deposit_date,
                        "orderNumber": "%s %s" % (sh.reason, sh.pack),
                        },
                    "parcel": {
                        "weight": "%s" % 0.3
                        },
                    "sender": {
                        "address": {
                            "companyName": "%s" %
                            sh.sender_address.company_name,
                            "firstName": "%s" % sh.sender_address.first_name,
                            "lastName": "%s" % sh.sender_address.last_name,
                            "line0": "",
                            "line1": "",
                            "line2": "%s" % sh.sender_address.street1,
                            "line3": "%s" % sh.sender_address.street2,
                            "countryCode": "%s" % sender_country,
                            "city": "%s" % sh.sender_address.city.strip(),
                            "zipCode": "%s" % (
                                sh.sender_address.zip_code.strip()),
                            }
                        },
                    "addressee": {
                        "address": {
                            "companyName": "%s" %
                            sh.recipient_address.company_name,
                            "firstName": "%s" %
                            sh.recipient_address.first_name,
                            "lastName": "%s" %
                            sh.recipient_address.last_name,
                            "line0": "",
                            "line1": "",
                            "line2": "%s" % sh.recipient_address.street1,
                            "line3": "%s" % sh.recipient_address.street2,
                            "countryCode": "%s" % recipient_country,
                            "city": "%s" % sh.recipient_address.city.strip(),
                            "zipCode": "%s" % (
                                sh.recipient_address.zip_code.strip()),
                            }
                        }
                    }
                }
        return data

    def create_label(self, shipment=None):
        url = \
            "https://ws.colissimo.fr/sls-ws/SlsServiceWSRest/generateLabel"
        data = self.map_data(shipment)
        req = requests.post(url, json=data)
        res = dict()

        # Parse multipart response
        multipart_data = decoder.MultipartDecoder.from_response(req)
        pdf = b''
        infos = dict()

        for part in multipart_data.parts:
            head = dict((item[0].decode(), item[1].decode()) for
                        item in part.headers.lower_items())
            if ("content-type" in head.keys() and
                head.get('content-type', None) ==
                    "application/octet-stream"):
                pdf = part.content
            elif ("content-type" in head.keys() and
                  head.get('content-type', None).startswith(
                      "application/json")):
                infos = json.loads(part.content.decode())

        if req.status_code in (400, 500):
            raise Exception(infos['messages'])
        elif req.status_code == 200:
            del data['contractNumber']
            del data['password']
            res['infos'] = infos
            res['pdf'] = pdf
            shipment.save_document(
                pdf,
                'application/pdf'
            )
            if not shipment.properties:
                shipment.properties = {
                    'sent': data,
                    'received': infos,
                }
            else:
                shipment.properties.update({
                    'sent': data,
                    'received': infos,
                })
            shipment.status = "label"
            shipment.tracking_number = infos['labelResponse']['parcelNumber']

        res['status_code'] = req.status_code
        return res

    def get_label_status(self, shipment=None):
        logger.info('Get label status for %r', shipment)
        url = (
            "https://www.coliposte.fr/tracking-chargeur-cxf"
            "/TrackingServiceWS/track")
        data = {"accountNumber": "%s" % self.credential.account_number,
                "password": "%s" % self.credential.password,
                "skybillNumber": shipment.tracking_number}
        req = requests.get(url, data)
        response = etree.fromstring(req.text)[0][0][0]  # ugly but only way
        res = {x.tag: x.text for x in response}
        if res['errorCode'] != '0':
            raise Exception(res['errorMessage'])

        properties = shipment.properties.copy()
        if 'events' not in properties:
            properties['events'] = {}
        else:
            properties['events'] = properties['events'].copy()

        if res['eventDate'] in properties['events']:
            return

        try:
            status = eventCodes[res['eventCode']]
            shipment.status = status
            properties['events'][res['eventDate']] = {
                'eventDate': res['eventDate'],
                'eventStatus': status,
                'eventLibelle': res['eventLibelle'],
            }
            shipment.properties = properties
            self.registry.flush()
            logger.info('%r status : %r', shipment, status)
        except KeyError:
            logger.exception("%r" % res)
            raise
