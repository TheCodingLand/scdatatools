from pathlib import Path


class SCLocalization:
    """Utilities for converting to localized strings"""

    def __init__(self, p4k, default_language="english", cache_dir=None):
        self.p4k = p4k
        self.default_language = default_language
        self.languages = []
        self.translations = {}

        if cache_dir is not None:
            if not (lcache := Path(cache_dir) / "localization").is_dir():
                lcache.mkdir(parents=True)
                self.p4k.extractall(members=self.p4k.search("Data/Localization/*/global.ini"), path=lcache, monitor=None)
            localization_files = lcache.rglob('**/global.ini')
        else:
            localization_files = self.p4k.search("Data/Localization/*/global.ini")

        for l in localization_files:
            with l.open('rb') as f:
                lang = f.name.split("/")[-2]
                self.languages.append(lang)
                self.translations[lang] = dict(
                    _.split("=", 1)
                    for _ in f.read().decode("utf-8").split("\r\n")
                    if "=" in _
                )

    def gettext(self, key, language=None):
        language = (
            self.default_language
            if (language is None or language not in self.languages)
            else language
        )
        trans = self.translations.get(language, {}).get(key, "")
        if not trans and key.startswith("@"):
            trans = self.translations.get(language, {}).get(key[1:], "")
        return trans if trans else key
