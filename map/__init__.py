# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Map-generation package shared by the CLI, web application, and agent tools.

The rendering entry point is auto_map.auto_map. Provider requests and idea
suggestions live in functions; geometry, styling, and legend own rendering
helpers. Import the focused module when adding behavior rather than putting
unrelated utilities in this package initializer.
"""
