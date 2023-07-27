from unittest import TestCase

from libvirt_lvm_vol import _detect_datacenter, _filter_images, is_dm_device, _parse_volgroup_volname


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

    def test__filter_images_no_versioning(self):
        images = [
            "base-debian-12",
        ]
        want = "base-debian-12"
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

    def test_is_dm_device(self):
        expected_output = {
            "/dev/mapper/am-arsch": True,
            "/dev/mapper/a-b": True,
            "/dev/mapper/libvirt_evo_970_1-nas": True,
            "/dev/mapper/": False,
            "/dev/sda": False,
        }

        for key in expected_output:
            want = expected_output[key]
            got = is_dm_device(key)
            if want != got:
                self.fail(f"expected {want}, got {got}")


    def test_extract_vg_lv(self):
        expected_output = {
            "/dev/mapper/vg-lv": ("vg", "lv"),
            "/dev/mapper/under_score-volume": ("under_score", "volume"),
            "/dev/mapper/libvirt_evo_970_1-fileserver": ("libvirt_evo_970_1", "fileserver"),
            "/dev/mapper/a-b": ("a", "b"),
        }

        for key in expected_output:
            want = expected_output[key]
            got = _parse_volgroup_volname(key)
            if want != got:
                self.fail(f"expected {want}, got {got}")

        def test_extract_vg_lv_anti(self):
            expected_output = [
                "",
                "vg-lv",
            ]

        for key in expected_output:
            try:
                _parse_volgroup_volname(key)
                self.fail(f"expected expection for input '{key}'")
            except ValueError:
                pass
