#pragma once

#include <string>

namespace localcodex::i18n {

void set_language(const std::string& language);
const std::string& language();
const char* tr(const char* key);

}  // namespace localcodex::i18n
