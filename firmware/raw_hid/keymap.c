// SPDX-License-Identifier: GPL-2.0-or-later
// Raw HID keymap for Work Louder Micro — per-key LED control via custom protocol
//
// Protocol (32-byte HID reports):
//   CMD 0x01: Set single LED    [0x01, led_idx, h, s, v]
//   CMD 0x02: Set LED range     [0x02, start, count, h1,s1,v1, h2,s2,v2, ...]
//   CMD 0x03: Restore effect    [0x03]  — exits direct mode, resumes normal RGB
//   CMD 0x04: Set all LEDs      [0x04, h, s, v]
//   CMD 0x05: Enter direct mode [0x05]  — responds [0x05, 0x01, led_count]
//   CMD 0x06: Set underglow      [0x06, h, s, v]  — sets all 8 underglow LEDs
//   CMD 0x07: Set blink          [0x07, led_idx, enable]  — 1=blink, 0=steady
//   CMD 0x08: Set blink speed    [0x08, period_ms_lo, period_ms_hi]  — default 500ms
//   CMD 0x09: Bootloader         [0x09, 0xB0, 0x07]  — reboot into bootloader (magic bytes required)
//   CMD 0x0A: Underglow breathe  [0x0A, h, s, v]  — breathing effect on underglow
//   CMD 0xEE: Key event (out)    [0xEE, row, col]  — sent by firmware on key press in direct mode
//   CMD 0xF0: Ping              [0xF0]  — responds [0xF0, 0x01, led_count]

#include QMK_KEYBOARD_H
#include "raw_hid.h"

#define NUM_LEDS 12

static bool     direct_mode = false;
static uint8_t  led_buf[NUM_LEDS][3]; // h, s, v per LED
static uint16_t blink_mask = 0;       // bit per LED: 1=blinking
static uint16_t blink_period = 500;   // ms per full on/off cycle

enum custom_keycodes {
    LED_LEVEL = QK_USER,
};

// ---- Helpers ----

static inline void set_led_rgb(uint8_t idx, uint8_t h, uint8_t s, uint8_t v) {
    HSV hsv = {.h = h, .s = s, .v = v};
    RGB rgb = hsv_to_rgb(hsv);
    rgb_matrix_set_color(idx, rgb.r, rgb.g, rgb.b);
}

// ---- Raw HID handler ----

void raw_hid_receive(uint8_t *data, uint8_t length) {
    uint8_t cmd = data[0];
    uint8_t response[32] = {0};
    response[0] = cmd;

    switch (cmd) {
        case 0x01: { // Set single LED — just update buffer, indicators callback renders
            uint8_t idx = data[1];
            if (idx < NUM_LEDS && direct_mode) {
                led_buf[idx][0] = data[2];
                led_buf[idx][1] = data[3];
                led_buf[idx][2] = data[4];
            }
            response[1] = 0x01;
            break;
        }
        case 0x02: { // Set LED range
            uint8_t start = data[1];
            uint8_t count = data[2];
            if (direct_mode && start + count <= NUM_LEDS && count <= 9) {
                for (uint8_t i = 0; i < count; i++) {
                    uint8_t idx = start + i;
                    led_buf[idx][0] = data[3 + i * 3];
                    led_buf[idx][1] = data[4 + i * 3];
                    led_buf[idx][2] = data[5 + i * 3];
                }
            }
            response[1] = 0x01;
            break;
        }
        case 0x03: { // Restore normal effect
            direct_mode = false;
            blink_mask = 0;
            response[1] = 0x01;
            break;
        }
        case 0x04: { // Set all LEDs same color
            if (direct_mode) {
                for (uint8_t i = 0; i < NUM_LEDS; i++) {
                    led_buf[i][0] = data[1];
                    led_buf[i][1] = data[2];
                    led_buf[i][2] = data[3];
                }
            }
            response[1] = 0x01;
            break;
        }
        case 0x05: { // Enter direct mode
            direct_mode = true;
            blink_mask = 0;
            memset(led_buf, 0, sizeof(led_buf));
            response[1] = 0x01;
            response[2] = NUM_LEDS;
            break;
        }
        case 0x06: { // Set underglow color (rgblight, 8 LEDs on D2)
            rgblight_mode_noeeprom(RGBLIGHT_MODE_STATIC_LIGHT);
            rgblight_sethsv_noeeprom(data[1], data[2], data[3]);
            response[1] = 0x01;
            break;
        }
        case 0x07: { // Set blink for individual LED
            uint8_t idx = data[1];
            if (idx < NUM_LEDS) {
                if (data[2]) {
                    blink_mask |= (1 << idx);
                } else {
                    blink_mask &= ~(1 << idx);
                }
            }
            response[1] = 0x01;
            break;
        }
        case 0x08: { // Set blink speed (period in ms)
            blink_period = data[1] | (data[2] << 8);
            if (blink_period < 50) blink_period = 50;
            response[1] = 0x01;
            break;
        }
        case 0x0A: { // Underglow breathing effect
            rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING);
            rgblight_sethsv_noeeprom(data[1], data[2], data[3]);
            response[1] = 0x01;
            break;
        }
        case 0x09: { // Reboot into bootloader (requires magic bytes 0xB0, 0x07)
            if (data[1] == 0xB0 && data[2] == 0x07) {
                response[1] = 0x01;
                raw_hid_send(response, sizeof(response));
                reset_keyboard();
            }
            response[1] = 0xFF; // wrong magic
            break;
        }
        case 0xF0: { // Ping
            response[1] = 0x01;
            response[2] = NUM_LEDS;
            break;
        }
        default: {
            response[1] = 0xFF;
            break;
        }
    }
    raw_hid_send(response, sizeof(response));
}

// Apply direct-mode colors after normal RGB effect renders each frame
bool rgb_matrix_indicators_user(void) {
    if (direct_mode) {
        bool blink_on = (timer_read() % blink_period) < (blink_period / 2);
        for (uint8_t i = 0; i < NUM_LEDS; i++) {
            if ((blink_mask & (1 << i)) && !blink_on) {
                rgb_matrix_set_color(i, 0, 0, 0);
            } else {
                set_led_rgb(i, led_buf[i][0], led_buf[i][1], led_buf[i][2]);
            }
        }
    }
    return true;
}

// ---- Standard keymap (matches stock Work Louder default) ----

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    LAYOUT(
        KC_MPLY, KC_9,    KC_0,    KC_NO,
        KC_5,    KC_6,    KC_7,    KC_8,
        KC_1,    KC_2,    KC_3,    KC_4,
        TO(1),   KC_DOT,  KC_COMM, LED_LEVEL
    ),
    LAYOUT(
        KC_1,    KC_2,    KC_3,    KC_4,
        KC_5,    KC_6,    KC_7,    KC_8,
        KC_9,    KC_0,    KC_A,    KC_B,
        TO(2),   KC_C,    KC_D,    KC_E
    ),
    LAYOUT(
        KC_1,    KC_2,    KC_3,    KC_4,
        KC_5,    KC_6,    KC_7,    KC_8,
        KC_9,    KC_0,    KC_A,    KC_B,
        TO(3),   KC_C,    KC_D,    KC_E
    ),
    LAYOUT(
        KC_1,    KC_2,    KC_3,    KC_4,
        KC_5,    KC_6,    KC_7,    KC_8,
        KC_9,    KC_0,    KC_A,    KC_B,
        TO(0),   KC_C,    LED_LEVEL, KC_E
    ),
};

typedef union {
    uint32_t raw;
    struct {
        uint8_t led_level : 3;
    };
} work_louder_config_t;

work_louder_config_t work_louder_config;

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    // Report key presses to daemon when in direct mode
    if (direct_mode && record->event.pressed) {
        uint8_t report[32] = {0};
        report[0] = 0xEE;
        report[1] = record->event.key.row;
        report[2] = record->event.key.col;
        raw_hid_send(report, sizeof(report));
    }

    switch (keycode) {
        case LED_LEVEL:
            if (record->event.pressed) {
                work_louder_config.led_level++;
                if (work_louder_config.led_level > 4) {
                    work_louder_config.led_level = 0;
                }
                work_louder_micro_led_all_set(
                    (uint8_t)(work_louder_config.led_level * 255 / 4));
                eeconfig_update_user(work_louder_config.raw);
                layer_state_set_kb(layer_state);
            }
            break;
    }
    return true;
}

#if defined(ENCODER_MAP_ENABLE)
const uint16_t PROGMEM encoder_map[][NUM_ENCODERS][NUM_DIRECTIONS] = {
    { ENCODER_CCW_CW(KC_VOLD, KC_VOLU), ENCODER_CCW_CW(C(KC_Z), C(KC_Y)) },
    { ENCODER_CCW_CW(_______, _______), ENCODER_CCW_CW(_______, _______) },
    { ENCODER_CCW_CW(_______, _______), ENCODER_CCW_CW(_______, _______) },
    { ENCODER_CCW_CW(_______, _______), ENCODER_CCW_CW(_______, _______) },
};
#endif

layer_state_t layer_state_set_user(layer_state_t state) {
    layer_state_cmp(state, 1) ? work_louder_micro_led_1_on() : work_louder_micro_led_1_off();
    layer_state_cmp(state, 2) ? work_louder_micro_led_2_on() : work_louder_micro_led_2_off();
    layer_state_cmp(state, 3) ? work_louder_micro_led_3_on() : work_louder_micro_led_3_off();
    return state;
}

void eeconfig_init_user(void) {
    work_louder_config.raw = 0;
    work_louder_config.led_level = 1;
    eeconfig_update_user(work_louder_config.raw);
}

void matrix_init_user(void) {
    work_louder_config.raw = eeconfig_read_user();
    work_louder_micro_led_all_set(
        (uint8_t)(work_louder_config.led_level * 255 / 4));
}
