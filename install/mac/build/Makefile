# Build the two release artifacts:
#   helper-latest.tgz — the helper service tarball
#   ABA-Setup.zip     — the zipped .command file users download
#
# Both are uploaded to GitHub Releases by the CI workflow.

ROOT       := $(shell git rev-parse --show-toplevel)
HELPER_SRC := $(ROOT)/install/mac/helper
SETUP_SRC  := $(ROOT)/install/mac/setup.command
PLIST_SRC  := $(HELPER_SRC)/src/aba_installer/templates/launchd.plist.template

OUT_DIR    := $(ROOT)/install/mac/build/out
HELPER_TGZ := $(OUT_DIR)/helper-latest.tgz
SETUP_ZIP  := $(OUT_DIR)/ABA-Setup.zip

VERSION    := $(shell grep -E '^version' $(HELPER_SRC)/pyproject.toml | head -1 | cut -d'"' -f2)

.PHONY: all helper setup clean

all: helper setup

$(OUT_DIR):
	mkdir -p $(OUT_DIR)

# ─── helper-latest.tgz ─────────────────────────────────────────────────────
helper: $(HELPER_TGZ)

$(HELPER_TGZ): $(OUT_DIR)
	@echo "Packing helper $(VERSION) into $@"
	@TMP=$$(mktemp -d) && \
	cp -R $(HELPER_SRC) "$$TMP/aba-installer" && \
	cp $(PLIST_SRC) "$$TMP/aba-installer/com.kharchenkolab.aba.helper.plist.template" && \
	cd "$$TMP" && \
	tar -czf $@ aba-installer && \
	rm -rf "$$TMP"
	@echo "  → $@ ($$(du -h $@ | cut -f1))"

# ─── ABA-Setup.zip (the user-facing download) ──────────────────────────────
setup: $(SETUP_ZIP)

$(SETUP_ZIP): $(SETUP_SRC) $(OUT_DIR)
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
