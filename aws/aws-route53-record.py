#!/usr/bin/env python3

import argparse
import ipaddress
import logging
import sys

from typing import Optional

import boto3


def get_hosted_zone_id(route53: boto3.client, hostname: str) -> Optional[str]:
    response = route53.list_hosted_zones()

    matching_zones = []
    for zone in response['HostedZones']:
        if hostname.endswith(zone['Name'][:-1]):
            matching_zones.append(zone)

    if matching_zones:
        matching_zones.sort(key=lambda zone: len(zone['Name']), reverse=True)
        return matching_zones[0]['Id']

    return None


def get_change_batch(hostname: str, ip_address: str, action: str = None, ttl: int = None, record_type: str = None) -> dict:
    if not action:
        action = "UPSERT"

    if not ttl or ttl < 60:
        ttl = 300

    try:
        ip_address = ipaddress.ip_address(ip_address)
    except ipaddress.AddressValueError:
        logging.info("Could not parse '%s' as ip address", ip_address)

    if record_type and record_type.lower() in ["aaaa", "a"]:
        if isinstance(ip_address, ipaddress.IPv4Address) and record_type.lower() == "aaaa":
            raise ValueError("you supplied an ipv4 address and record_type AAAA")
        elif isinstance(ip_address, ipaddress.IPv6Address) and record_type.lower() == "a":
            raise ValueError("you supplied an ipv6 address and record_type A")

    if not record_type:
        if isinstance(ip_address, ipaddress.IPv4Address):
            logging.info("Automatically setting record_type=A for supplied IPv4 (%s)", ip_address)
            record_type = "A"
        elif isinstance(ip_address, ipaddress.IPv6Address):
            logging.info("Automatically setting record_type=AAAA for supplied IPv6 (%s)", ip_address)
            record_type = "AAAA"

    if not record_type:
        raise ValueError("could not automatically detect ip address")

    # Create a new DNS record for the hostname with the provided TTL and type
    return {
        'Changes': [
            {
                'Action': action.upper(),
                'ResourceRecordSet': {
                    'Name': hostname,
                    'Type': record_type,
                    'TTL': ttl,
                    'ResourceRecords': [{'Value': str(ip_address)}],
                }
            }
        ]
    }


def main(route53: boto3.client, args: argparse.Namespace) -> None:
    # Get the Route53 hosted zone ID dynamically based on the hostname
    zone_id = args.hosted_zone
    if not zone_id:
        zone_id = get_hosted_zone_id(route53, args.hostname)
        if not zone_id:
            logging.error("No hosted_zone not found for hostname '%s'", args.hostname)
            sys.exit(1)

    logging.info("Found hosted_zone '%s' for hostname '%s'", zone_id, args.hostname)
    change_batch = get_change_batch(args.hostname, args.ip_address, args.action, args.ttl, args.type)
    route53.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch=change_batch)
    logging.info("Hostname '%s' %sed successfully in hosted zone %s", args.hostname, args.action, zone_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Set resource records via Route53.")
    parser.add_argument("action", choices=["upsert", "delete"], help="Choose 'upsert' to update/insert or 'delete' to delete the DNS record.")
    parser.add_argument("hostname", type=str, help="The hostname you want to set.")
    parser.add_argument("ip_address", type=str, help="The IP address to associate with the hostname.")
    parser.add_argument("--hosted-zone", type=str, help="The hosted_zone id")

    parser.add_argument("--ttl", type=int, default=300, help="Optional TTL for the new DNS record (default is 300).")
    parser.add_argument("--type", type=str, help="Optional type for the new DNS record.")
    args = parser.parse_args()
    route53 = boto3.client('route53')

    main(route53, args)
