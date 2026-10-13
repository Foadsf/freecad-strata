# SPDX-License-Identifier: LGPL-2.1-or-later
"""Core tests: no FreeCAD needed. Run: python -m unittest discover -s tests"""

import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from strata import docdiff  # noqa: E402
from strata.project import Project, StrataError, next_revision  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "Bracket.FCStd")
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))


def entries(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def write(path, ents):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in ents.items():
            z.writestr(n, d)


def set_prop(path, obj, prop, value):
    """Change one property value the way FreeCAD would write it."""
    ents = entries(path)
    xml = ents["Document.xml"].decode()
    start = xml.index('<Object name="%s"' % obj, xml.index("<ObjectData"))
    p = xml.index('<Property name="%s"' % prop, start)
    m = re.compile(r'value="[^"]*"').search(xml, p)
    xml = xml[:m.start()] + 'value="%s"' % value + xml[m.end():]
    ents["Document.xml"] = xml.encode()
    write(path, ents)


def get_prop(path, obj, prop):
    xml = entries(path)["Document.xml"].decode()
    start = xml.index('<Object name="%s"' % obj, xml.index("<ObjectData"))
    p = xml.index('<Property name="%s"' % prop, start)
    return re.compile(r'value="([^"]*)"').search(xml, p).group(1)


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="strata-test-")
        self.root = os.path.join(self.tmp, "Gearbox")
        os.makedirs(os.path.join(self.root, "Parts"))
        self.doc = os.path.join(self.root, "Parts", "Bracket.FCStd")
        ents = entries(FIXTURE)
        ents["thumbnails/Thumbnail.png"] = PNG
        write(self.doc, ents)
        Project._instances.clear()
        self.p = Project.create(self.root, "Ada", "ada@example.com")

    def tearDown(self):
        for p in Project._instances.values():
            p.repo.close()
        Project._instances.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- basics
    def test_start_tracks_text_only(self):
        h = self.p.history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0].title, "Start")
        files = self.p.files(self.p.head())
        self.assertIn("Parts/Bracket.FCStd/Document.xml", files)
        self.assertIn("Parts/Bracket.FCStd/Pad.Shape.brp", files)
        self.assertNotIn("Parts/Bracket.FCStd/thumbnails/Thumbnail.png", files)  # binary stays local
        self.assertEqual(self.p.current_workspace(), "main")

    def test_record_describes_parameter_change(self):
        set_prop(self.doc, "Pad", "Length", "10")
        sha = self.p.record(["Parts/Bracket.FCStd"])
        self.assertIsNotNone(sha)
        self.assertEqual(self.p.history()[0].title, "Edited Pad: Length 7 mm → 10 mm")
        self.assertIsNone(self.p.record(["Parts/Bracket.FCStd"]), "no change, no history entry")

    def test_version_and_restore_roundtrip(self):
        before = entries(self.doc)
        v1 = self.p.create_version("V1", "First prototype")
        self.assertEqual(self.p.suggest_version_name(), "V2")
        set_prop(self.doc, "Pad", "Length", "12")
        self.p.record()
        changed = self.p.restore(v1.sha)
        self.assertEqual(changed, ["Parts/Bracket.FCStd"])
        after = entries(self.doc)
        self.assertEqual(before, after, "restore must reproduce every entry, binaries included")
        self.assertEqual(self.p.history()[0].title, "Restored to V1")
        self.assertEqual(len(self.p.history()), 3, "restore adds history; nothing is lost")

    def test_workspaces_isolate_changes(self):
        self.p.create_version("V1")
        slug, _ = self.p.create_workspace("Light weight", description="try thinner walls")
        self.assertEqual(slug, "Light_weight")
        self.assertEqual(self.p.current_workspace(), "Light_weight")
        set_prop(self.doc, "Pad", "Length", "4")
        self.p.record()
        changed = self.p.switch_workspace("main")
        self.assertEqual(changed, ["Parts/Bracket.FCStd"])
        self.assertEqual(float(get_prop(self.doc, "Pad", "Length")), 7)
        self.p.switch_workspace("Light_weight")
        self.assertEqual(float(get_prop(self.doc, "Pad", "Length")), 4)
        names = [w.display for w in self.p.workspaces()]
        self.assertEqual(names, ["Main", "Light weight"])

    def test_merge_clean_and_conflict(self):
        self.p.create_workspace("Feature")
        set_prop(self.doc, "Box", "Length", "30")
        self.p.record()
        self.p.switch_workspace("main")
        plan = self.p.merge_plan("Feature")
        self.assertEqual(plan.take_theirs, ["Parts/Bracket.FCStd"])
        self.assertEqual(plan.conflicts, [])
        self.p.merge(plan)
        self.assertEqual(float(get_prop(self.doc, "Box", "Length")), 30)
        self.assertTrue(self.p.history()[0].title.startswith("Merged Feature into Main"))
        # now both sides edit the same document
        self.p.switch_workspace("Feature")
        set_prop(self.doc, "Pad", "Length", "9")
        self.p.record()
        self.p.switch_workspace("main")
        set_prop(self.doc, "Pad", "Length", "11")
        self.p.record()
        plan = self.p.merge_plan("Feature")
        self.assertEqual(plan.conflicts, ["Parts/Bracket.FCStd"])
        with self.assertRaises(StrataError):
            self.p.merge(plan)
        self.p.merge(plan, {"Parts/Bracket.FCStd": "both"})
        self.assertEqual(float(get_prop(self.doc, "Pad", "Length")), 11)
        copy = os.path.join(self.root, "Parts", "Bracket (Feature).FCStd")
        self.assertEqual(float(get_prop(copy, "Pad", "Length")), 9)

    def test_survives_git_gc(self):
        v1 = self.p.create_version("V1")
        self.p.create_workspace("Alt", switch=False)
        set_prop(self.doc, "Pad", "Length", "6")
        self.p.record()
        self.p.repo.git("gc", "--quiet", "--prune=now")  # packs objects and refs
        self.p.repo._cache.clear()
        self.assertFalse(os.path.isfile(os.path.join(self.p.repo.git_dir, "refs", "tags", "v", "V1")))
        self.assertEqual([v.name for v in self.p.versions()], ["V1"])
        self.assertEqual(len(self.p.history()), 2)
        self.assertEqual([w.name for w in self.p.workspaces()], ["main", "Alt"])
        self.p.restore(v1.sha)
        self.assertEqual(float(get_prop(self.doc, "Pad", "Length")), 7)
        self.p.delete_workspace("Alt")
        self.assertEqual([w.name for w in self.p.workspaces()], ["main"])

    def test_graph(self):
        self.p.create_version("V1")
        self.p.create_workspace("Alt")
        set_prop(self.doc, "Pad", "Length", "5")
        self.p.create_version("Alt-1")
        nodes = self.p.graph()
        kinds = {tuple(n.versions): n.lane for n in nodes if n.versions}
        self.assertEqual(kinds[("V1",)], 0)
        self.assertEqual(kinds[("Alt-1",)], 1)
        self.assertEqual(nodes[-1].parents, [])

    def test_releases(self):
        v1 = self.p.create_version("V1")
        r = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100", description="Initial")
        self.assertEqual((r.revision, r.state), ("A", "Released"))
        set_prop(self.doc, "Pad", "Length", "8")
        v2 = self.p.create_version("V2")
        r2 = self.p.release(v2, "Parts/Bracket.FCStd", "BRK-100", approval=True)
        self.assertEqual((r2.revision, r2.state), ("B", "Pending"))
        self.p.set_release_state(r2, "Released")
        self.p.set_release_state(r, "Obsolete")
        states = {x.revision: x.state for x in self.p.releases()}
        self.assertEqual(states, {"A": "Obsolete", "B": "Released"})
        self.assertEqual(next_revision(["H"]), "J")  # I is skipped (ASME Y14.35)
        self.assertEqual(next_revision(["Y"]), "AA")

    def test_resubmit_after_rejection(self):
        v1 = self.p.create_version("V1")
        r = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100", approval=True)
        self.p.set_release_state(r, "Rejected")
        self.assertEqual(self.p.next_revision("BRK-100"), "A", "a rejected label is free again")
        r2 = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100", approval=True)  # same label, second attempt
        self.assertEqual((r2.revision, r2.state), ("A", "Pending"))
        self.p.set_release_state(r2, "Released")
        states = [(x.revision, x.state) for x in self.p.releases() if x.part_number == "BRK-100"]
        self.assertIn(("A", "Released"), states)

    def test_obsolete_re_releasable(self):
        v1 = self.p.create_version("V1")
        r = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100")
        self.p.set_release_state(r, "Obsolete")
        self.assertEqual(self.p.next_revision("BRK-100"), "B", "a plain obsolete keeps its label")
        r_b = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-200")
        self.p.set_release_state(r_b, "Obsolete", reusable=True)
        self.assertEqual(self.p.next_revision("BRK-200"), "A", "re-releasable obsolete frees its label")
        again = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-200")
        self.assertEqual((again.revision, again.state), ("A", "Released"))

    def test_numeric_revision_scheme(self):
        self.p.save_settings({"revision_scheme": "numeric"})
        v1 = self.p.create_version("V1")
        r1 = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100")
        r2 = self.p.release(v1, "Parts/Bracket.FCStd", "BRK-100")
        self.assertEqual((r1.revision, r2.revision), ("1", "2"))
        self.assertEqual(self.p.revision_history("BRK-100")[0].revision, "2", "newest revision first")

    def test_merge_strategies(self):
        shaft = os.path.join(self.root, "Shaft.FCStd")
        shutil.copy(self.doc, shaft)
        self.p.record()
        self.p.create_workspace("Feature")
        set_prop(self.doc, "Box", "Length", "30")       # changed only in Feature
        self.p.record()
        self.p.switch_workspace("main")
        set_prop(shaft, "Box", "Length", "40")          # changed only in Main
        self.p.record()
        plan = self.p.merge_plan("Feature")
        self.assertEqual((plan.take_theirs, plan.ours_only), (["Parts/Bracket.FCStd"], ["Shaft.FCStd"]))
        # Keep Main: nothing changes, but history records the merge
        self.p.merge(plan, strategy="keep")
        self.assertEqual(float(get_prop(self.doc, "Box", "Length")), 25)
        self.assertTrue(self.p.history()[0].title.startswith("Merged Feature into Main"))
        # Undo the merge (allowed right after it), then Replace with source: both become Feature's
        self.assertTrue(self.p.can_undo_merge())
        self.p.undo_merge()
        self.assertFalse(self.p.history()[0].title.startswith("Merged"), "the merge left history")
        plan = self.p.merge_plan("Feature")
        self.p.merge(plan, strategy="replace")
        self.assertEqual(float(get_prop(self.doc, "Box", "Length")), 30)
        self.assertEqual(float(get_prop(shaft, "Box", "Length")), 25, "Main's own change replaced by Feature's")
        # Merge changes with a per-document override: keep Main's Bracket
        self.p.undo_merge()
        plan = self.p.merge_plan("Feature")
        self.p.merge(plan, {"Parts/Bracket.FCStd": "ours"})
        self.assertEqual(float(get_prop(self.doc, "Box", "Length")), 25)
        self.assertEqual(float(get_prop(shaft, "Box", "Length")), 40)

    def test_properties_merge_automatically(self):
        """One side edits only the document's properties, the other the model: no conflict, both kept."""
        def set_comment(value):
            ents = entries(self.doc)
            xml = ents["Document.xml"].decode()
            p = xml.index('<Property name="Comment"')
            m = re.compile(r'value="[^"]*"').search(xml, p)
            ents["Document.xml"] = (xml[:m.start()] + 'value="%s"' % value + xml[m.end():]).encode()
            write(self.doc, ents)

        self.p.create_workspace("Feature")
        set_prop(self.doc, "Box", "Length", "30")
        self.p.record()
        self.p.switch_workspace("main")
        set_comment("Bracket, laser cut")
        self.p.record()
        plan = self.p.merge_plan("Feature")
        self.assertEqual((plan.conflicts, plan.auto_merge), ([], {"Parts/Bracket.FCStd": "ours"}))
        self.p.merge(plan)
        self.assertEqual(float(get_prop(self.doc, "Box", "Length")), 30)
        self.assertEqual(self.p.document_properties("Parts/Bracket.FCStd")["Description"], "Bracket, laser cut")
        # a real conflict stays one
        self.p.undo_merge()
        set_prop(self.doc, "Box", "Length", "35")
        self.p.record()
        self.assertEqual(self.p.merge_plan("Feature").conflicts, ["Parts/Bracket.FCStd"])

    def test_sync_between_two_engineers(self):
        shared = os.path.join(self.tmp, "share")
        self.p.set_remote(shared)
        self.p.create_version("V1")
        res = self.p.sync()
        self.assertIn("main", res.pushed)
        bob_root = os.path.join(self.tmp, "bob", "Gearbox")
        bob = Project.join(shared, bob_root, "Bob", "bob@example.com")
        bob_doc = os.path.join(bob_root, "Parts", "Bracket.FCStd")
        self.assertEqual(float(get_prop(bob_doc, "Pad", "Length")), 7)
        self.assertEqual([v.name for v in bob.versions()], ["V1"])
        set_prop(bob_doc, "Pad", "Length", "13")
        bob.sync()
        res = self.p.sync()
        self.assertIn("main", res.pulled)
        self.assertEqual(float(get_prop(self.doc, "Pad", "Length")), 13)
        self.assertEqual(self.p.history()[0].author, "Bob")


class DiffTest(unittest.TestCase):
    def test_renumbered_side_files_are_not_a_change(self):
        """FreeCAD renames colour-array side files (LineColorArray3 → LineColorArray1) on every save."""
        xml = b'<Document><Objects><Object type="Part::Box" name="Box"/></Objects><ObjectData></ObjectData></Document>'

        def gui(f):
            return ('<Document><ViewProviderData><ViewProvider name="Box"><Properties>'
                    '<Property name="LineColorArray" type="App::PropertyColorList"><ColorList file="%s"/></Property>'
                    '</Properties></ViewProvider></ViewProviderData></Document>' % f).encode()
        same = docdiff.diff_documents(xml, xml, {"LineColorArray3": "h1"}, {"LineColorArray1": "h1"},
                                      gui("LineColorArray3"), gui("LineColorArray1"))
        self.assertTrue(same.empty)
        recoloured = docdiff.diff_documents(xml, xml, {"LineColorArray3": "h1"}, {"LineColorArray1": "h2"},
                                            gui("LineColorArray3"), gui("LineColorArray1"))
        self.assertEqual(recoloured.modified[0].appearance, ["LineColorArray"])

    def test_external_link_stamp_is_not_a_change(self):
        def doc(stamp, name="Body"):
            return ('<Document><Objects><Object type="App::Link" name="L"/></Objects><ObjectData>'
                    '<Object name="L"><Properties><Property name="LinkedObject" type="App::PropertyXLink">'
                    '<XLink file="Parts/Bracket.FCStd" stamp="%s" name="%s"/></Property></Properties></Object>'
                    '</ObjectData></Document>' % (stamp, name)).encode()
        self.assertTrue(docdiff.diff_documents(doc("2026-01-01"), doc("2026-02-02")).empty)
        d = docdiff.diff_documents(doc("2026-01-01"), doc("2026-02-02", "Body001"))
        self.assertEqual(docdiff.summarize(d), "Edited L: LinkedObject Bracket ▸ Body → Bracket ▸ Body001")

    def test_summary_language(self):
        xml = entries(FIXTURE)["Document.xml"]
        new = xml.replace(b'<Object type="Part::Box" name="Box" id="4126" />', b"")
        d = docdiff.diff_documents(xml, new)
        self.assertEqual([x[0] for x in d.removed], ["Box"])
        self.assertIn("Deleted Box", docdiff.summarize(d))


if __name__ == "__main__":
    unittest.main()
