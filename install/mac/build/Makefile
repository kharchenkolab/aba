# Build the single release artifact:
#   ABA-Setup.zip — the zipped .command file users download.
#
# That's the whole rollout: the .command clones the repo and installs the
# helper from it (see ../setup.command), so there is no separate helper
# bundle to ship. Uploaded to GitHub Releases (or just linked from the README).

ROOT       := $(shell git rev-parse --show-toplevel)
SETUP_SRC  := $(ROOT)/install/mac/setup.command

OUT_DIR    := $(ROOT)/install/mac/build/out
SETUP_ZIP  := $(OUT_DIR)/ABA-Setup.zip

.PHONY: all setup clean

all: setup

$(OUT_DIR):
	mkdir -p $(OUT_DIR)

# ─── ABA-Setup.zip (the user-facing download) ──────────────────────────────
setup: $(SETUP_ZIP)

$(SETUP_ZIP): $(SETUP_SRC) | $(OUT_DIR)
	@echo "Packing setup.command into $@"
	@TMP=$$(mktemp -d) && \
	cp $(SETUP_SRC) "$$TMP/ABA Setup.command" && \
	chmod 0755 "$$TMP/ABA Setup.command" && \
	cd "$$TMP" && \
	zip -q $@ "ABA Setup.command" && \
	rm -rf "$$TMP"
	@echo "  → $@ ($$(du -h $@ | cut -f1))"

clean:
	rm -rf $(OUT_DIR)
