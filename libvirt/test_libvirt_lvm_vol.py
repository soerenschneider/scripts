from unittest import TestCase

from libvirt_lvm_vol import _detect_datacenter, _filter_images


class Test(TestCase):
    def test_detect_datacenter_happy_path(self):
        expected_output = {
            "name.dd.domain.tld": "dd",
            "name.ha.ez.domain.tld": "ez",
            "service.prd.ha.pt.domain.tld": "pt",
            "service.prd.ha.pt.verylongdomainname.t": "pt",
        }

        for key in expected_output:
            want = expected_output[key]
            got = _detect_datacenter(key)
            if want != got:
                self.fail(f"expected {want}, got {got}")
            else:
                print(f"expected {want}, got {got}")

    def test_detect_datacenter(self):
        anti_examples = [
            "www.google.de",
            "some.other.fr.domain.tld",
        ]

        for example in anti_examples:
            want = None
            got = _detect_datacenter(example)
            if want != got:
                self.fail(f"expected {want}, got {got}")

    def test__filter_images_almalinux(self):
        images = [
            "AlmaLinux-9-GenericCloud-9.2-20230301.x86_64.qcow2",
            "AlmaLinux-9-GenericCloud-9.2-20230513.x86_64.qcow2",
            "AlmaLinux-9-GenericCloud-9.2-20230412.x86_64.qcow2",
            "garbage"
        ]
        want = "AlmaLinux-9-GenericCloud-9.2-20230513.x86_64.qcow2"

        got = _filter_images(images)

        if want != got:
            self.fail(f"wanted {want}, got {got}")

    def test__filter_images_debian(self):
        images = [
            "garbage",
            "debian-12-generic-amd64-20220612-0009.qcow2",
            "debian-12-generic-amd64-20230612-1409.qcow2",
            "debian-12-generic-amd64-20230530-1922.qcow2",
        ]
        want = "debian-12-generic-amd64-20230612-1409.qcow2"

        got = _filter_images(images)

        if want != got:
            self.fail(f"wanted {want}, got {got}")
