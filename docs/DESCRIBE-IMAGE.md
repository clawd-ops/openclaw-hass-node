# Describe an image

The **Describe image** service turns a photo into a single line of text, using
the vision model your OpenClaw gateway is already set up with. A typical use is
a doorbell: when someone rings, you get a notification that says what the
camera sees.

```text
openclaw_hass_node_assist.describe_image
```

It returns the description as a service response, so you use it from an
automation or script and read the result with `response_variable`.

## Before you start

- The OpenClaw HA Node app, the **OpenClaw HA Node — Assist** integration and
  the gateway plugin are all installed and up to date, and the integration is
  set up.
- Your gateway has an image-capable model available. If you already ask your
  agent about photos, you are set.

## Fields

Give **exactly one** image source. Everything else is optional.

| Field | What it does |
| --- | --- |
| `camera_entity_id` | Takes a snapshot from this camera. |
| `media_content_id` | Uses an image from a media source, for example `media-source://media_source/local/porch.jpg`. |
| `file_path` | Uses an image file. The folder must be one Home Assistant allows (see `allowlist_external_dirs`). |
| `style` | How the line should sound: `straightforward`, `serious` or `funny`, or a few words of your own such as `dry and sarcastic` (up to 200 characters). Defaults to the style set in the integration options. |
| `instructions` | A hint about what to look for, such as `Mention any packages` (up to 300 characters). |

The service returns:

```yaml
text: A delivery driver is leaving a package on the front step.
```

Large images are shrunk before they are sent, so a full-resolution snapshot is
fine.

## How long it takes

A description usually takes **20 to 40 seconds**. Plan for that: do not make
anything time-critical wait for it. The pattern below tells you immediately that
someone rang, then replaces the message with the description when it arrives.

If a new request comes in for the same camera while an older one is still
running, the older one stops with a `SUPERSEDED` error and the newer one
continues. This keeps a doorbell that is pressed repeatedly from piling up
requests.

## Choosing the model

By default the description uses your agent's own model. If your agent runs on a
CLI backend (for example the Claude CLI), that model has no direct API access
and the service fails with `VISION_UNAVAILABLE`. Set the app option
`vision_model` to a `provider/model` that does, such as `openai/gpt-6-astra`.

The app waits up to 45 seconds by default. Change this with the
`vision_timeout_seconds` option in the app configuration.

## Example: doorbell notification

```yaml
automation:
  - alias: Doorbell with description
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door_doorbell
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Doorbell
          message: Someone is at the front door.
          data:
            tag: front-door
      - action: openclaw_hass_node_assist.describe_image
        data:
          camera_entity_id: camera.front_door
          style: straightforward
          instructions: Mention any packages.
        response_variable: description
        continue_on_error: true
      - if:
          - condition: template
            value_template: "{{ description is defined and description.text is defined }}"
        then:
          - action: notify.mobile_app_phone
            data:
              title: Doorbell
              message: "{{ description.text }}"
              data:
                tag: front-door
```

Both notifications use the same `tag`, so the description replaces the first
message on the phone instead of adding a second one.

## Choosing a default style

Open **Settings → Devices & services → OpenClaw HA Node — Assist → Configure**
and pick a default style, or type your own. A `style` given in a service call
always wins.

## If something goes wrong

The service raises an error that starts with a short code.

| Code | Meaning |
| --- | --- |
| `TIMEOUT` | The description took longer than the app's limit. Raise `vision_timeout_seconds`. |
| `SUPERSEDED` | A newer request for the same source replaced this one. |
| `VISION_UNAVAILABLE` | The vision model returned nothing or could not be reached. Set `vision_model` to an image-capable `provider/model`. |
| `VISION_PLUGIN_MISSING` | The gateway plugin is out of date. Update it. |
| `NODE_UNREACHABLE` | Home Assistant could not reach the app. |
| `IMAGE_UNREADABLE`, `IMAGE_TOO_LARGE`, `IMAGE_PATH_NOT_ALLOWED` | The image source could not be used. |
| `NOT_PAIRED`, `OPERATOR_NOT_CONNECTED` | The app is not connected to the gateway. |
