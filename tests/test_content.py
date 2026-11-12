import json

import pytest

from connector import content

OK_PACKAGE = """
template:
  - sensor:
      - name: hfb_x
        state: "{{ states('sensor.a') | float(0) * 2 }}"
input_number:
  hfb_x:
    min: 0
    max: 10
homeassistant:
  customize:
    sensor.hfb_x:
      friendly_name: X
sensor:
  - platform: template
    sensors: {}
automation:
  - id: hfb_x
    triggers:
      - trigger: state
        entity_id: sensor.a
    actions:
      - action: script.hfb_notify
        data:
          message: hi
      - action: script.hfb_notify_{{ channel }}
      - service: light.turn_on
        target: {entity_id: "{{ 'light.' ~ x }}"}
"""


def test_a_usual_package_passes():
    assert content.check_yaml("packages/hfb_x.yaml", OK_PACKAGE) is None
    assert content.check_yaml("packages/empty.yaml", "") is None


@pytest.mark.parametrize(
    "text",
    [
        "a: !env_var X\n",
        "a: !secret x\n",
        "!secret k: v\n",
        "s:\n  - action: !secret x\n",
        "sensor:\n  - platform: !secret x\n",
        "b: !input y\n",
        "x: !include ../themes/evil.yaml\n",
        "automation: !include_dir_merge_list ../themes\n",
        "a: !python/object:os.system x\n",
    ],
)
def test_no_tag_is_accepted(text):
    assert content.check_yaml("packages/x.yaml", text).startswith("tag '!")
    assert content.check_tags(text).startswith("tag '!")


def test_a_theme_passes():
    assert content.check_tags("hfb:\n  primary-color: '#fff'\n") is None


@pytest.mark.parametrize(
    "text, refused",
    [
        *[(f"{k}:\n  x: y\n", k) for k in sorted(content.FORBIDDEN_KEYS)],
        *[
            (f"homeassistant:\n  {k}: x\n", f"homeassistant: {k}")
            for k in sorted(content.HOMEASSISTANT_KEYS)
        ],
        *[
            (f"{d}:\n  - platform: {p}\n    command: x\n", f"platform: {p}")
            for d in ("sensor", "notify", "switch", "binary_sensor")
            for p in sorted(content.FORBIDDEN_PLATFORMS)
        ],
        *[
            (f"script:\n  s:\n    sequence:\n      - action: {json.dumps(a)}\n", f"action: {a}")
            for a in (
                "shell_command.x",
                "rest_command.x",
                "python_script.x",
                "pyscript.x",
                "hassio.addon_stdin",
                "hassio.host_reboot",
                "hassio.host_shutdown",
                "hassio.addon_start",
                "hassio.addon_stop",
                "hassio.addon_restart",
                "Shell_Command.x",
                "{{ 'shell_command.x' }}",
                "{% if 1 %}shell_command.x{% endif %}",
            )
        ],
        # nested: choose / then / sequence inside an automation, old `service:` key
        (
            "automation:\n  - actions:\n      - choose:\n          - conditions: []\n"
            "            sequence:\n              - if: []\n                then:\n"
            "                  - service: shell_command.x\n",
            "service: shell_command.x",
        ),
        ("script:\n  s:\n    sequence:\n      - service_template: '{{ x }}'\n", "service_template"),
        ("a: [unclosed\n", "neplatný YAML"),
    ],
)
def test_refused(text, refused):
    assert refused in (content.check_yaml("packages/x.yaml", text) or "")


def test_alias_bomb_is_walked_once():
    lines = ["a0: &a0 [x]"] + [f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]" for i in range(1, 40)]
    assert content.check_yaml("packages/x.yaml", "\n".join(lines) + "\n") is None


def test_automation_body():
    ok = {"triggers": [], "actions": [{"action": "script.hfb_notify"}]}
    assert content.check_automation_body(json.dumps(ok).encode()) is None
    bad = {"sequence": [{"parallel": [{"action": "hassio.addon_stdin", "data": {}}]}]}
    assert content.check_automation_body(json.dumps(bad).encode()) == "action: hassio.addon_stdin"
    assert content.check_automation_body(b"{") == "neplatný JSON"


def test_automation_body_against_the_live_config():
    """A rename fix of the user's own automation: its forbidden action was there before."""
    live = {
        "triggers": [{"entity_id": "sensor.old"}],
        "actions": [{"action": "hassio.addon_restart"}],
    }
    renamed = json.dumps({**live, "triggers": [{"entity_id": "sensor.new"}]}).encode()
    assert content.check_automation_body(renamed, live) is None
    twice = {**live, "actions": live["actions"] * 2}  # one more than live: new
    assert content.check_automation_body(json.dumps(twice).encode(), live) == (
        "action: hassio.addon_restart"
    )
    assert content.check_automation_body(renamed, None) == "action: hassio.addon_restart"


def test_dashboard():
    ok = {"views": [{"cards": [{"type": "tile", "tap_action": {"action": "more-info"}}]}]}
    assert content.check_dashboard(ok) is None
    for action in (
        {"action": "perform-action", "perform_action": "shell_command.x"},
        {"action": "call-service", "service": "hassio.addon_stdin"},
    ):
        bad = {"views": [{"cards": [{"type": "button", "hold_action": action}]}]}
        assert content.check_dashboard(bad) is not None
        assert content.check_dashboard(bad, bad) is None  # the user's own card stays
        ours = {"views": [*bad["views"], {"cards": [{"type": "tile"}]}]}
        assert content.check_dashboard(ours, bad) is None  # Homecraft adds a section


@pytest.mark.parametrize(
    "text, refused",
    [
        ("shell_command 2:\n  x: id\n", "shell_command"),
        ("Rest_Command evil:\n  x: {url: x}\n", "rest_command"),
        *[(f"{k}:\n  x: y\n", k) for k in ("frontend", "lovelace", "ffmpeg", "logger")],
        ("homeassistant:\n  media_dirs: {m: /}\n", "homeassistant: media_dirs"),
        ("homeassistant:\n  customize_glob: {}\n", "homeassistant: customize_glob"),
        ("influxdb:\n  host: evil.example\n", "influxdb: host"),
        ("influxdb:\n", "influxdb: ?"),
        *[
            (f"script:\n  s:\n    sequence:\n      - action: {json.dumps(a)}\n", f"action: {a}")
            for a in (
                "downloader.download_file",
                "backup.create",
                "update.install",
                "homeassistant.stop",
                "homeassistant.restart",
                "homeassistant.{{ x }}",
                "recorder.purge",
                "recorder.purge_entities",
                "recorder.disable",
            )
        ],
    ],
)
def test_refused_second_round(text, refused):
    assert content.check_yaml("packages/x.yaml", text) == refused


def test_the_librarys_services_pass():
    ok = "influxdb:\n  include: {entity_globs: [sensor.hfb_ltm_*]}\n  ignore_attributes: [icon]\n"
    assert content.check_yaml("packages/x.yaml", ok) is None
    assert content.check_yaml("packages/x.yaml", "recorder:\n  purge_keep_days: 30\n") is None
    for a in ("homeassistant.update_entity", "recorder.get_statistics", "notify.mobile_app_x"):
        assert not content.service_refused(a)


USER = [
    {"id": "u_tab", "type": "tab", "disabled": True},
    {"id": "u_exec", "type": "exec", "z": "u_tab", "command": "ls"},
    {"id": "u_srv", "type": "server", "name": "Home Assistant"},
    {"id": "u_sf", "type": "subflow", "in": [{"wires": [{"id": "u_sf_x"}]}]},
    {"id": "u_sf_x", "type": "exec", "z": "u_sf", "command": "ls"},
]
CALL = {
    "type": "api-call-service",
    "version": 7,
    "blockInputOverrides": True,
    "domain": "climate",
    "service": "set_temperature",
    "action": "climate.set_temperature",
}
OURS = [
    {"id": "hfb_t", "type": "tab", "info": "ha-builder: heating@1"},
    {"id": "hfb_t_s", "type": "subflow", "in": [{"wires": [{"id": "hfb_t_sf"}]}]},
    {"id": "hfb_t_sf", "type": "function", "z": "hfb_t_s", "libs": [], "wires": [[]]},
    {"id": "hfb_t_f", "type": "function", "z": "hfb_t", "wires": [["hfb_t_i"]]},
    {"id": "hfb_t_i", "type": "subflow:hfb_t_s", "z": "hfb_t", "wires": [["hfb_t_c"]]},
    {"id": "hfb_t_c", "z": "hfb_t", **CALL},
]


def flows(nodes, rev="r1") -> bytes:
    return json.dumps({"rev": rev, "flows": nodes}).encode()


def check(nodes, live=()) -> str | None:
    return content.check_nodered_flows(flows(nodes), {"rev": "r1", "flows": list(live)})


def test_nodered_only_added_or_changed_nodes_are_checked():
    assert check(USER + OURS, USER) is None
    assert content.check_nodered_flows(json.dumps(USER + OURS).encode(), USER) is None  # API v1
    assert check(USER + OURS, USER + OURS) is None  # the next Apply
    changed = [*USER[:1], {**USER[1], "command": "curl evil"}, *USER[2:]]
    assert check(changed, USER) == "změna uživatelova obsahu u_exec"
    assert content.check_nodered_flows(flows(USER), None) == "uzel exec"  # nothing to compare


def test_nodered_reach_through_into_user_nodes():
    to_exec = {"id": "hfb_t_n", "type": "inject", "z": "hfb_t", "wires": [["u_exec"]]}
    assert check(USER + OURS + [to_exec], USER) == "uzel exec"
    dangling = {**to_exec, "wires": [["gone"]]}
    assert check(USER + OURS + [dangling], USER) == "drát z hfb_t_n do neznámého uzlu"
    user_sf = {"id": "hfb_t_u", "type": "subflow:u_sf", "z": "hfb_t"}
    assert check(USER + OURS + [user_sf], USER) == "uzel exec"  # the subflow's insides count
    no_def = {"id": "hfb_t_u", "type": "subflow:nope", "z": "hfb_t"}
    assert check(USER + OURS + [no_def], USER) == "uzel subflow:nope bez definice"


def test_nodered_user_tabs_and_subflows_stay_as_they_are():
    enabled = [{**USER[0], "disabled": False}, *USER[1:]]
    assert check(enabled + OURS, USER) == "změna uživatelova obsahu u_tab"
    rewired = [*USER[:3], {**USER[3], "in": []}, USER[4]]
    assert check(rewired + OURS, USER) == "změna uživatelova obsahu u_sf"
    assert check(OURS, USER) == "změna uživatelova obsahu u_tab"  # dropped
    assert check([*USER[:4], *OURS], USER) == "změna uživatelova obsahu u_sf_x"  # dropped
    ours_changed = [{**OURS[0], "disabled": True}, {**OURS[1], "name": "x"}, *OURS[2:]]
    assert check(USER + ours_changed, USER + OURS) is None
    hijack = [{**USER[0], "info": "ha-builder: x@1", "disabled": False}, *USER[1:]]
    assert check(hijack, USER) == "změna uživatelova obsahu u_tab"  # the live tab decides
    into = {"id": "hfb_t_n", "type": "inject", "z": "u_tab"}
    assert check(USER + OURS + [into], USER) == "uzel hfb_t_n v uživatelově tab u_tab"
    into_sf = {**into, "z": "u_sf"}
    assert check(USER + OURS + [into_sf], USER) == "uzel hfb_t_n v uživatelově subflow u_sf"


LIBS = [{"var": "cp", "module": "child_process"}]


@pytest.mark.parametrize(
    "node, refused",
    [
        ({"type": "exec", "command": "id"}, "uzel exec"),
        ({"type": "ha-sensor"}, "uzel ha-sensor"),
        ({"type": "ha-action", "action": "light.turn_on"}, "uzel ha-action"),
        ({"type": "server"}, "uzel server"),
        ({"type": "function", "libs": LIBS}, "uzel function s moduly (libs)"),
        (
            {**CALL, "action": "shell_command.x", "domain": "shell_command", "service": "x"},
            "uzel api-call-service se službou shell_command.x",
        ),
        (
            {**CALL, "action": None, "domain": "hassio", "service": "addon_stdin"},
            "uzel api-call-service se službou hassio.addon_stdin",
        ),
        (
            {**CALL, "action": "{{ msg.a }}", "domain": None},
            "uzel api-call-service se službou {{ msg.a }}",
        ),
        (
            {**CALL, "action": "", "domain": "", "service": ""},
            "uzel api-call-service bez jednoznačné služby",
        ),
        ({**CALL, "action": "shell_command.x"}, "uzel api-call-service bez jednoznačné služby"),
        ({**CALL, "blockInputOverrides": False}, "uzel api-call-service bez blockInputOverrides"),
        (
            {k: v for k, v in CALL.items() if k != "blockInputOverrides"},
            "uzel api-call-service bez blockInputOverrides",
        ),
        ({**CALL, "version": 6}, "uzel api-call-service verze 6"),
        ({**CALL, "service_domain": "shell_command"}, "uzel api-call-service verze 7"),
    ],
)
def test_nodered_refused(node, refused):
    assert check([{"id": "n", **node}]) == refused


def test_nodered_library_shaped_call_passes():
    assert check([{"id": "n", **CALL}]) is None
    assert check([{"id": "n", **CALL, "action": None}]) is None  # domain + service only


def test_nodered_bad_body():
    assert content.check_nodered_flows(b"{", []) == "neplatný JSON flows"
    assert content.check_nodered_flows(b'{"flows": 1}', []) == "neplatný JSON flows"
    assert check([{"type": "tab"}]) == "uzel bez id"
    assert check(["x"]) == "uzel bez id"
    assert check([{"id": "n"}]) == "uzel bez typu"


GO2RTC = """
api: {listen: ":1984"}
streams:
  cam: exec:ffmpeg -i rtsp://x -f rtsp {output}
  door: rtsp://u:p@10.0.0.5/s
webrtc:
  listen: ":8555"
"""


def test_go2rtc_only_the_librarys_keys_change():
    new = GO2RTC.replace('":8555"', '":8556"\n  candidates: ["10.0.0.2:8555"]')
    assert content.check_go2rtc_config(new.encode(), GO2RTC) is None  # user's exec: stays
    assert content.check_go2rtc_config(b"webrtc: {listen: ':8555'}\n", "") is None  # no file
    without_ours = GO2RTC.split("webrtc:")[0]
    assert content.check_go2rtc_config(without_ours.encode(), GO2RTC) is None
    library = 'webrtc:\n  listen: ":8555"\n  candidates: ["10.0.0.2:8555"]\n'
    library += '  ice_servers:\n    - urls: ["stun:stun.l.google.com:19302"]\n'
    assert content.check_go2rtc_config(library.encode(), "") is None
    with_var = 'api: {username: "${USER}"}\n' + library
    assert content.check_go2rtc_config(with_var.encode(), with_var) is None  # the user's own


@pytest.mark.parametrize(
    "evil, refused",
    [
        (GO2RTC.replace("rtsp://u:p@10.0.0.5/s", "echo:/bin/sh -c id"), "klíč streams"),
        (GO2RTC + "ffmpeg:\n  bin: /bin/sh\n", "klíč ffmpeg"),
        (GO2RTC.replace("door:", "new: exec:id\n  door:"), "klíč streams"),
        ("webrtc: {}\n", "klíč api"),  # the user's keys dropped
        (GO2RTC.replace('api: {listen: ":1984"}', "api:"), "klíč api"),  # nulled
        (GO2RTC.replace('api: {listen: ":1984"}', ""), "klíč api"),  # dropped
        ("- x\n", "go2rtc.yaml, který není mapa"),
        ("a: [\n", "neplatný YAML"),
        (GO2RTC.replace('listen: ":8555"', 'listen: "${HOME}"'), "proměnná ${…}"),
        (GO2RTC.replace('listen: ":8555"', "filters: {candidates: [x]}"), "tvar klíče webrtc"),
        (GO2RTC.replace('listen: ":8555"', "listen: [1]"), "tvar klíče webrtc"),
        (
            GO2RTC.replace('listen: ":8555"', "ice_servers: [{urls: [x], credential: y}]"),
            "tvar klíče webrtc",
        ),
    ],
)
def test_go2rtc_refused(evil, refused):
    assert content.check_go2rtc_config(evil.encode(), GO2RTC) == refused


def test_refused_error():
    e = content.refused("packages/x.yaml", "shell_command")
    assert e.code == "refused_content"
    assert e.message == "packages/x.yaml: shell_command není přes Homecraft Connector povolený"
