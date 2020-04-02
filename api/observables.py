from abc import ABCMeta, abstractmethod
from http import HTTPStatus
from itertools import chain
from typing import Optional, Dict, Any, Iterable, List
from urllib.parse import quote
from uuid import uuid4

import requests

from .token import headers


class Observable(metaclass=ABCMeta):
    """Represents an observable."""

    @staticmethod
    def of(type_: str) -> Optional['Observable']:
        """Returns an instance of `Observable` for the specified type."""

        for cls in Observable.__subclasses__():
            if cls.type() == type_:
                return cls()

        return None

    @staticmethod
    @abstractmethod
    def type() -> str:
        """Returns the observable type."""

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Returns the name of the observable type.

        The name must be suitable to be used in sentences like "Search for this
        {name}". For example, an observable of type 'md5' should have a name
        'MD5', 'file_path' should have a name 'file path', etc.
        """

    def observe(self, api: str, observable: str) -> Dict[str, Any]:
        """Retrieves objects (sightings, verdicts, etc.) for an observable."""

        def fetch(active_):
            if active_:
                url = f'{api}/ioc/events?state=true&' \
                      f'filter={self.filter(observable)}'
            else:
                url = f'{api}/ioc/events?' \
                      f'filter={self.filter(observable)}'

            response = requests.get(url, headers=headers())

            # Refresh the token if expired.
            if response.status_code == HTTPStatus.UNAUTHORIZED.value:
                response = requests.get(url, headers=headers(fresh=True))

            response.raise_for_status()

            return response.json()

        observed = {}

        for active in [True, False]:
            for event in fetch(active):
                for obj, docs in self.map(observable, event, active).items():
                    observed[obj] = observed.get(obj, []) + docs

        return observed

    def refer(self, api: str, observable: str) -> str:
        """Returns a URL for pivoting back to Qualys."""
        return f'{api}/ioc/#/hunting?search={quote(self.filter(observable))}'

    def map(self,
            observable: str,
            event: Dict[str, Any],
            active: bool) -> Dict[str, List]:
        """Maps Qualys' events to CTIM objects."""

        confidence = 'High'
        severity = 'Medium'
        schema = '1.0.16'

        def sighting():
            return {
                'id': f'transient:{uuid4()}',
                'confidence': confidence,
                'count': 1,
                'external_ids': [
                    get(event, '.id')
                ],
                'external_references': [],
                'observables': [
                    {
                        'type': self.type(),
                        'value': observable
                    }
                ],
                'observed_time': {
                    'start_time': get(event, '.dateTime')
                },
                'relations': list(relations(event)),
                'schema_version': schema,
                'severity': severity,
                'sensor': 'endpoint',
                'source': 'Qualys IOC',
                'targets': [
                    {
                        'observables': list(targets(event)),
                        'observed_time': {
                            'start_time': get(event, '.dateTime')
                        },
                        'type': 'endpoint',
                        'os': get(event, '.asset.fullOSName')
                    }
                ],
                'type': 'sighting',
                'description': f'A Qualys IOC event related to "{observable}"',
                'data': {
                    'columns': [{'name': 'Active', 'type': 'string'}],
                    'rows': [[str(active)]],
                    'row_count': 1
                },
            }

        def judgement(indicator2):
            verdict = indicator2.get('verdict')

            disposition = {
                'KNOWN': 1,
                'UNKNOWN': 5,
                'MALICIOUS': 2,
                'REMEDIATED': 2,
            }.get(verdict) or 5

            disposition_name = {
                'KNOWN': 'Clean',
                'UNKNOWN': 'Unknown',
                'MALICIOUS': 'Malicious',
                'REMEDIATED': 'Malicious',  # May be changed later.
            }.get(verdict) or 'Unknown'

            return {
                'id': f'transient:{uuid4()}',
                'confidence': confidence,
                'disposition': disposition,
                'disposition_name': disposition_name,
                'external_ids': [
                    get(event, '.id')
                ],
                'external_references': [],
                'observable': {
                    'type': self.type(),
                    'value': observable
                },
                'priority': 90,
                'reason': indicator2.get('threatName', ''),
                'schema_version': schema,
                'severity': severity,
                'source': 'Qualys IOC',
                'type': 'judgement',
                'valid_time': {}
            }

        def indicator():
            return {
                'id': f'transient:{uuid4()}',
                'type': 'indicator',
                'schema_version': schema,
                'source': 'Qualys IOC',
                'producer': 'Qualys IOC',
                'severity': severity,
                'valid_time': {},
                'external_ids': [
                    get(event, '.id')
                ],
                'confidence': confidence,
            }

        def relationship(sources_, type_, targets_):
            for source in sources_:
                for target in targets_:
                    yield {
                        'id': f'transient:{uuid4()}',
                        'type': 'relationship',
                        'schema_version': schema,
                        'source': 'Qualys IOC',
                        'source_uri': '',
                        'source_ref': source['id'],
                        'target_ref': target['id'],
                        'relationship_type': type_,
                        'external_ids': []
                    }

        sightings = [sighting()]
        judgements = [judgement(x) for x in get(event, '.indicator2') or []]
        indicators = [indicator()]

        relationships = list(chain(
            relationship(judgements, 'based-on', indicators),
            relationship(sightings, 'based-on', judgements),
            relationship(sightings, 'sighting-of', indicators),
        ))

        return {
            'sightings': sightings,
            'judgements': judgements,
            'indicators': indicators,
            'relationships': relationships
        }

    @abstractmethod
    def filter(self, observable: str) -> str:
        """Returns a filter to search for the provided observable."""


class MD5(Observable):

    @staticmethod
    def type() -> str:
        return 'md5'

    @staticmethod
    def name() -> str:
        return 'MD5'

    def filter(self, observable: str) -> str:
        return f'file.hash.md5: "{observable}"'


class SHA256(Observable):

    @staticmethod
    def type() -> str:
        return 'sha256'

    @staticmethod
    def name() -> str:
        return 'SHA256'

    def filter(self, observable: str) -> str:
        return f'file.hash.sha256: "{observable}"'


class FileName(Observable):

    @staticmethod
    def type() -> str:
        return 'file_name'

    @staticmethod
    def name() -> str:
        return 'file name'

    def filter(self, observable: str) -> str:
        return f'file.name: "{observable}"'


class FilePath(Observable):

    @staticmethod
    def type() -> str:
        return 'file_path'

    @staticmethod
    def name() -> str:
        return 'file path'

    def filter(self, observable: str) -> str:
        return f'file.fullPath: "{observable}"'


class IP(Observable):

    @staticmethod
    def type() -> str:
        return 'ip'

    @staticmethod
    def name() -> str:
        return 'IP'

    def filter(self, observable: str) -> str:
        return (f'network.local.address.ip: "{observable}" or '
                f'network.remote.address.ip: "{observable}"')


class Domain(Observable):

    @staticmethod
    def type() -> str:
        return 'domain'

    @staticmethod
    def name() -> str:
        return 'domain'

    def filter(self, observable: str) -> str:
        return f'network.remote.address.fqdn: "{observable}"'


class Mutex(Observable):

    @staticmethod
    def type() -> str:
        return 'mutex'

    @staticmethod
    def name() -> str:
        return 'mutex'

    def filter(self, observable: str) -> str:
        return f'handle.name: "{observable}"'


def get(event: Dict[str, Any], path: str, default: Any = None) -> Any:
    """Returns a value by the specified path if such exists or default."""

    result = event
    parts = iter(path.split('.'))

    # Skip the first entry.
    # It is always empty due to the leading period.
    next(parts)

    for part in parts:
        if part in result:
            result = result[part]
        else:
            return default

    return result


def relations(event: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Constructs relations based on the provided event."""

    def mapped(relations_):
        for source, relation, target in relations_:
            source_type, source_path = source
            target_type, target_path = target

            source_value = get(event, source_path)
            target_value = get(event, target_path)

            if source_value and target_value:
                yield {
                    "origin": 'Qualys IOC',
                    "related": {
                        "type": target_type,
                        "value": target_value
                    },
                    "relation": relation,
                    "source": {
                        "type": source_type,
                        "value": source_value
                    }
                }

    return mapped([
        # Relations from `.file`.
        (['file_name', '.file.fileName'], 'File_Name_Of',
         ['sha256',    '.file.sha256']),
        (['file_name', '.file.fileName'], 'File_Name_Of',
         ['md5',       '.file.md5']),
        (['file_path', '.file.fullPath'], 'File_Path_Of',
         ['sha256',    '.file.sha256']),
        (['file_path', '.file.fullPath'], 'File_Path_Of',
         ['md5',       '.file.md5']),

        # Relations from `.process`.
        (['file_name', '.process.processName'], 'Connected_To',
         ['ip',        '.network.remoteIP']),
        (['file_name', '.process.processName'], 'Connected_To',
         ['domain',    '.network.remoteDns']),

        # Relations from `.network`.
        (['ip',     '.network.remoteIP'], 'Resolved_To',
         ['domain', '.network.remoteDns']),
    ])


def targets(event: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Constructs targets based on the provided event."""

    if event['asset'].get('netBiosName'):
        yield {'type': 'hostname', 'value': event['asset']['netBiosName']}

    for interface in event['asset'].get('interfaces') or []:
        if interface.get('ipAddress'):
            yield {'type': 'ip', 'value': interface['ipAddress']}
        if interface.get('macAddress'):
            yield {'type': 'mac_address', 'value': interface['macAddress']}
