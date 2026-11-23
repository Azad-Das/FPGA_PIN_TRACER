import pytest

from fpga_pin_tracer.export import to_csv, to_txt
from fpga_pin_tracer.mating import norm_signal, suggest_mates
from fpga_pin_tracer.model import Mate
from fpga_pin_tracer.parser import parse_pdf
from fpga_pin_tracer.roles import annotate
from fpga_pin_tracer.trace import Design

from . import synth


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("pdf")
    paths = {"FPGA": d / "fpga.pdf", "SENSOR": d / "sensor.pdf", "BP": d / "backplane.pdf"}
    synth.fpga_board(str(paths["FPGA"]))
    synth.sensor_board(str(paths["SENSOR"]))
    synth.backplane(str(paths["BP"]))
    return {k: str(v) for k, v in paths.items()}


@pytest.fixture(scope="module")
def boards(pdfs):
    return {k: parse_pdf(p, name=k) for k, p in pdfs.items()}


def test_fpga_board_parsed(boards):
    b = boards["FPGA"]
    u1 = b.components["U1"]
    assert u1.part == "XC7K325T-2FFG900C"
    assert u1.pins["AE12"].name == "IO_L1P_T0_65"
    assert u1.pins["AE12"].net == "SENS_SDA"
    assert b.components["J1"].pins["H7"].net == "SENS_SDA"


def test_single_board_trace(boards):
    d = Design()
    d.add_board(boards["FPGA"])
    res = d.trace_component("FPGA", "J1")
    by_pin = {r.start.pin: r for r in res}
    assert by_pin["H7"].end.pin == "AE12"
    assert by_pin["H7"].bank == "65"
    assert by_pin["H7"].bank_voltage == "VCC1V8"
    assert by_pin["H8"].end.pin == "AF12"
    assert "H1" not in by_pin            # ground is never traced


def test_sensor_board_passives_and_shifter(boards):
    b = boards["SENSOR"]
    r12 = b.components["R12"]
    assert len(r12.pins) == 2
    assert r12.part == "33"
    annotate(b)
    assert b.components["U7"].role == "buffer"


def test_two_boards_through_level_shifter(boards):
    d = Design()
    d.add_board(boards["FPGA"])
    d.add_board(boards["SENSOR"])
    sug = suggest_mates(d.boards)
    assert [(s.mate.ref_a, s.mate.ref_b) for s in sug] in ([("J1", "J2")], [("J2", "J1")])
    d.mates = [s.mate for s in sug]
    res = {r.start.pin_name: r for r in d.trace_component("SENSOR", "U5")}
    sda, scl = res["SDA"], res["SCL"]
    assert (sda.end.board, sda.end.refdes, sda.end.pin) == ("FPGA", "U1", "AE12")
    assert (scl.end.board, scl.end.pin) == ("FPGA", "AF12")
    notes = " | ".join(h.note for h in scl.hops)
    assert "R12" in notes and "A2→B2" in notes and "⇄" in notes
    assert "A1→B1" in " | ".join(h.note for h in sda.hops)


def test_three_boards_through_backplane(boards):
    d = Design()
    for k in ("FPGA", "SENSOR", "BP"):
        d.add_board(boards[k])
    d.mates = [Mate("SENSOR", "J2", "BP", "J10"), Mate("BP", "J11", "FPGA", "J1")]
    res = {r.start.pin_name: r for r in d.trace_component("SENSOR", "U5")}
    sda = res["SDA"]
    assert sda.end.pin == "AE12" and sda.bank == "65"
    path = [h.note for h in sda.hops if "⇄" in h.note]
    assert path == ["SENSOR/J2.H7 ⇄ BP/J10.H7", "BP/J11.H7 ⇄ FPGA/J1.H7"]
    assert "AE12" in to_csv([sda]) and "Bank 65" in to_txt([sda])


def test_peripheral_list(boards):
    d = Design()
    d.add_board(boards["FPGA"])
    d.add_board(boards["SENSOR"])
    d.mates = [Mate("FPGA", "J1", "SENSOR", "J2")]
    refs = {(b, r) for b, r, _ in d.peripherals()}
    assert ("SENSOR", "U5") in refs and ("FPGA", "J1") in refs
    assert ("FPGA", "U1") not in refs


def test_norm_signal():
    assert norm_signal("FMC-LA01_CC_P") == norm_signal("LA01_P_CC".replace("_P_CC", "_CC_P")) == "LA01_P"
    assert norm_signal("FMC_LA06_N") == norm_signal("LA06_N")


def test_cadence_style_board(tmp_path):
    """Loose-stroke bodies, doubled 'm l h S' strokes, pin-cell connector, port-style labels."""
    p = tmp_path / "cad.pdf"
    synth.cadence_fpga_board(str(p))
    b = parse_pdf(str(p), name="CAD")
    annotate(b)
    u1, p1 = b.components["U1"], b.components["P1"]
    assert u1.role == "fpga" and p1.role == "connector"
    assert len(u1.pins) == 3                      # no twin pins from doubled strokes
    assert u1.pins["A9"].name == "IO_L24P_T3U_N10_70"
    assert u1.pins["A9"].net == "FMC_LA11_P"
    assert p1.pins["H2"].net == "FMC_LA11_P" and p1.pins["H3"].net == "FMC_LA11_N"
    d = Design()
    d.add_board(b)
    res = {r.start.pin: r for r in d.trace_component("CAD", "P1")}
    assert res["H2"].end.pin == "A9" and res["H2"].bank == "70"
    assert res["H3"].end.pin == "A8"


def test_diff_pair_termination_is_not_series():
    from fpga_pin_tracer.roles import _diff_pair
    assert _diff_pair("HSCI_CKIN_P", "HSCI_CKIN_N")
    assert _diff_pair("LA01_P_CC", "LA01_N_CC")
    assert not _diff_pair("GPIO1", "GPIO2")
    assert not _diff_pair("PCLK", "NCLK")


def test_ghost_text_boards(tmp_path):
    """Letters drawn as strokes with an invisible text layer (SCH2PDF style)."""
    fp, sp = tmp_path / "gf.pdf", tmp_path / "gs.pdf"
    synth.fpga_board(str(fp), cls=synth.GhostTextSheet)
    synth.sensor_board(str(sp), cls=synth.GhostTextSheet)
    fpga = parse_pdf(str(fp), name="FPGA")
    sensor = parse_pdf(str(sp), name="SENSOR")
    assert fpga.components["U1"].pins["AE12"].net == "SENS_SDA"
    d = Design()
    d.add_board(fpga)
    d.add_board(sensor)
    d.mates.append(Mate("FPGA", "J1", "SENSOR", "J2"))
    res = {r.start.pin_name: r for r in d.trace_component("SENSOR", "U5")}
    assert res["SDA"].end.pin == "AE12"
    assert res["SCL"].end.pin == "AF12"


def test_ghost_text_with_offpage_flags(tmp_path):
    """Labels behind off-page flags, KN connectors, TD-prefixed ICs, auto-mated by pin match."""
    fp, ap = tmp_path / "kd.pdf", tmp_path / "ad.pdf"
    synth.ghost_fpga_board(str(fp))
    synth.ghost_adc_board(str(ap))
    kd, ad = parse_pdf(str(fp), name="KD"), parse_pdf(str(ap), name="AD")
    d = Design()
    d.add_board(kd)
    d.add_board(ad)
    assert kd.components["TD10"].role == "fpga"
    assert kd.components["KN5"].role == "connector" and ad.components["KN1"].role == "connector"
    td = kd.components["TD10"].pins
    assert td["N36"].name == "IO_L6P_GC_XCC_N2P0_712" and td["N36"].net == "FMCP1_LA02_P"
    assert td["M37"].name == "IO_L6N_GC_XCC_N2P1_712" and td["M37"].net == "FMCP1_LA02_N"
    assert td["M32"].name == "IO_L7N_N2P3_712"
    mates = [s.mate for s in suggest_mates(d.boards)]
    assert {(m.ref_a, m.ref_b) for m in mates} & {("KN5", "KN1"), ("KN1", "KN5")}
    d.mates += mates
    res = {r.start.pin_name: r for r in d.trace_component("AD", "TD3")}
    assert res["GPIO_17"].end.pin == "N36" and res["GPIO_17"].bank == "712"
    assert res["GPIO_18"].end.pin == "M37"
